//! Strategy artifact loader.
//!
//! Resolves the `_strategy_gpt_{create,drop,abi_major}` symbols emitted by
//! [`engine_rt::strategy_entry!`] inside an LLM-compiled `cdylib`, performs
//! an ABI-major compatibility check against the worker's `RUNNER_VERSION`,
//! and hands out [`PluginStrategy`] instances tied to the plugin's
//! lifetime. The instance dereferences to a `&mut dyn Strategy` the
//! executor can drive through its usual lifecycle.
//!
//! See spec `strategy-runtime` requirement "Strategies load via cdylib +
//! C-ABI registration macro" for the contract this module enforces.
//!
//! ABI / same-toolchain caveat
//! ---------------------------
//! `Box<dyn Strategy>` is **not** part of Rust's stable ABI. The loader
//! assumes the plugin was built with the same toolchain and the same
//! `engine-rt` version as the worker — both guaranteed by the build
//! pipeline running under the workspace's `rust-toolchain.toml` pin.
//! Loading a plugin built with a different toolchain is undefined
//! behavior and is not supported.

use std::ffi::c_void;
use std::marker::PhantomData;
use std::path::Path;

use engine_rt::{Strategy, RUNNER_VERSION};
use libloading::{Library, Symbol};

type CreateFn = unsafe extern "C" fn() -> *mut c_void;
type DropFn = unsafe extern "C" fn(*mut c_void);
type AbiMajorFn = unsafe extern "C" fn() -> u16;

const SYM_CREATE: &[u8] = b"_strategy_gpt_create\0";
const SYM_DROP: &[u8] = b"_strategy_gpt_drop\0";
const SYM_ABI_MAJOR: &[u8] = b"_strategy_gpt_abi_major\0";

/// Errors returned by [`StrategyPlugin::load`].
#[derive(Debug, thiserror::Error)]
pub enum PluginError {
    #[error("failed to open plugin `{path}`: {source}")]
    Open {
        path: String,
        #[source]
        source: libloading::Error,
    },
    #[error("plugin missing required symbol `{symbol}`")]
    MissingSymbol { symbol: &'static str },
    #[error("plugin ABI major {plugin_major} incompatible with runner ABI major {runner_major}")]
    AbiMismatch {
        plugin_major: u16,
        runner_major: u16,
    },
}

/// Loaded strategy artifact.
///
/// Owns the underlying `cdylib`. Dropping the plugin closes the library;
/// all outstanding [`PluginStrategy`] instances borrow from it and are
/// statically prevented from outliving it.
#[derive(Debug)]
pub struct StrategyPlugin {
    create_sym: CreateFn,
    drop_sym: DropFn,
    abi_major: u16,
    // `_lib` is intentionally last so the library closes after every
    // strategy instance it created has been dropped via PluginStrategy.
    _lib: Library,
}

impl StrategyPlugin {
    /// Load a strategy plugin from disk and verify ABI compatibility.
    pub fn load(path: impl AsRef<Path>) -> Result<Self, PluginError> {
        let p = path.as_ref();
        // SAFETY: opening a `cdylib` runs its initializers; the artifacts
        // produced by the build pipeline are trusted to do nothing on load
        // (no `ctor` / `#[link_section = ".init_array"]`) — guaranteed by
        // the source linter rejecting `extern crate ctor` etc.
        let lib = unsafe { Library::new(p) }.map_err(|source| PluginError::Open {
            path: p.display().to_string(),
            source,
        })?;

        // SAFETY: symbol lookups are by name only; we copy the raw fn
        // pointers out below so the `Symbol` borrow ends before we return.
        let create_sym = unsafe { resolve::<CreateFn>(&lib, SYM_CREATE, "_strategy_gpt_create")? };
        let drop_sym = unsafe { resolve::<DropFn>(&lib, SYM_DROP, "_strategy_gpt_drop")? };
        let abi_sym =
            unsafe { resolve::<AbiMajorFn>(&lib, SYM_ABI_MAJOR, "_strategy_gpt_abi_major")? };

        // SAFETY: `abi_sym` is a `#[no_mangle] extern "C"` function with
        // no arguments that returns u16. Safe to invoke.
        let plugin_major = unsafe { abi_sym() };
        if plugin_major != RUNNER_VERSION.major {
            return Err(PluginError::AbiMismatch {
                plugin_major,
                runner_major: RUNNER_VERSION.major,
            });
        }

        Ok(Self {
            create_sym,
            drop_sym,
            abi_major: plugin_major,
            _lib: lib,
        })
    }

    /// Major ABI version reported by the loaded plugin.
    pub fn abi_major(&self) -> u16 {
        self.abi_major
    }

    /// Construct a fresh strategy instance from the plugin's factory.
    ///
    /// The returned [`PluginStrategy`] borrows from this plugin and will
    /// release the underlying allocation via the plugin's `drop` symbol
    /// when dropped.
    pub fn create(&self) -> PluginStrategy<'_> {
        // SAFETY: `create_sym` is a `#[no_mangle] extern "C"` factory
        // emitted by `strategy_entry!`; it returns a `*mut Box<dyn Strategy>`
        // owned by the plugin.
        let raw = unsafe { (self.create_sym)() };
        PluginStrategy {
            raw,
            drop_sym: self.drop_sym,
            _plugin: PhantomData,
        }
    }
}

/// Owned strategy instance produced by a [`StrategyPlugin`].
///
/// Cannot outlive its owning plugin. Dereferences mutably to `dyn Strategy`
/// so the executor can drive the usual `on_init` / `on_bar` / … lifecycle.
pub struct PluginStrategy<'a> {
    raw: *mut c_void,
    drop_sym: DropFn,
    _plugin: PhantomData<&'a StrategyPlugin>,
}

impl PluginStrategy<'_> {
    /// Mutable view of the underlying strategy.
    ///
    /// The reference borrows from `self` (and transitively the plugin),
    /// preventing use-after-free across plugin unload.
    pub fn strategy_mut(&mut self) -> &mut dyn Strategy {
        // SAFETY: `raw` originates from `_strategy_gpt_create` which
        // returns a `*mut Box<dyn Strategy>`; we cast back to the outer
        // box and deref through it to the inner box's trait object.
        unsafe {
            let outer = self.raw.cast::<Box<dyn Strategy>>();
            (*outer).as_mut()
        }
    }
}

impl Drop for PluginStrategy<'_> {
    fn drop(&mut self) {
        // SAFETY: `raw` was produced by the matching `_strategy_gpt_create`
        // and has not been dropped before (drop is idempotent against
        // null inside the plugin).
        unsafe { (self.drop_sym)(self.raw) };
    }
}

/// Look up a `#[no_mangle] extern "C"` symbol and copy the raw fn pointer
/// out so the caller is not tied to the `Symbol` lifetime.
///
/// SAFETY: caller asserts `T` matches the symbol's actual signature.
unsafe fn resolve<T: Copy>(
    lib: &Library,
    raw_name: &[u8],
    pretty_name: &'static str,
) -> Result<T, PluginError> {
    let sym: Symbol<T> = unsafe { lib.get(raw_name) }.map_err(|_| PluginError::MissingSymbol {
        symbol: pretty_name,
    })?;
    Ok(*sym)
}
