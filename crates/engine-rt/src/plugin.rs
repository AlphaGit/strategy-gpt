//! Plugin entry-point macro for LLM-emitted strategy crates.
//!
//! Strategies live in `cdylib` crates that depend on `engine-rt` and
//! implement the sealed [`Strategy`] trait. The author invokes
//! [`strategy_entry!`] once with a `fn() -> Box<dyn Strategy>` factory; the
//! macro emits three `#[no_mangle] extern "C"` symbols that the engine
//! worker resolves via `libloading`:
//!
//! - `_strategy_gpt_create` — constructs a strategy and returns an opaque
//!   `*mut Box<dyn Strategy>` pointer.
//! - `_strategy_gpt_drop` — drops a previously-created strategy. Always
//!   called by the worker exactly once per `create` to ensure the strategy
//!   is freed in the allocator that constructed it.
//! - `_strategy_gpt_abi_major` — returns `RUNNER_VERSION.major`. The
//!   worker checks this against its own `RUNNER_VERSION.major` before
//!   using the plugin and refuses incompatible artifacts.
//!
//! Strategy authors do **not** write `unsafe` or `extern "C"` themselves.
//! The macro's expansion contains both; the trusted `engine-rt` source is
//! exempt from the strategy linter, and the linter sees only the macro
//! invocation (`syn` parses it as an `Item::Macro` rather than recursing
//! into the expansion).
//!
//! ABI / same-toolchain caveat
//! ---------------------------
//! `Box<dyn Strategy>` crossing the `cdylib` boundary is **not** part of
//! Rust's stable ABI. The system assumes the strategy artifact and the
//! engine worker were compiled with the same Rust toolchain and the same
//! `engine-rt` version — both invariants hold by construction (the build
//! pipeline drives `cargo build` under the same toolchain pin from
//! `rust-toolchain.toml`). Cross-toolchain plugin loading is undefined
//! behavior and is not supported.
//!
//! See spec `strategy-runtime` requirement "Strategies load via cdylib +
//! C-ABI registration macro".

/// Emit the C-ABI registration symbols for a strategy cdylib.
///
/// Usage in an LLM-emitted strategy crate:
///
/// ```ignore
/// use engine_rt::{strategy_entry, Strategy, /* ... */};
///
/// struct MyStrategy { /* … */ }
/// impl engine_rt::Sealed for MyStrategy {}
/// impl Strategy for MyStrategy { /* … */ }
///
/// fn new_strategy() -> Box<dyn Strategy> {
///     Box::new(MyStrategy { /* … */ })
/// }
///
/// strategy_entry!(new_strategy);
/// ```
#[macro_export]
macro_rules! strategy_entry {
    ($factory:expr) => {
        /// Allocate a strategy instance owned by the plugin allocator.
        ///
        /// SAFETY: the returned pointer must be freed via the matching
        /// `_strategy_gpt_drop` exported by the same plugin.
        #[no_mangle]
        pub extern "C" fn _strategy_gpt_create() -> *mut ::core::ffi::c_void {
            let factory: fn() -> ::std::boxed::Box<dyn $crate::Strategy> = $factory;
            let inner = factory();
            let outer: ::std::boxed::Box<::std::boxed::Box<dyn $crate::Strategy>> =
                ::std::boxed::Box::new(inner);
            ::std::boxed::Box::into_raw(outer).cast::<::core::ffi::c_void>()
        }

        /// Drop a strategy instance allocated by `_strategy_gpt_create`.
        ///
        /// SAFETY: `ptr` must have been returned by the matching
        /// `_strategy_gpt_create` and must not have been dropped already.
        /// Passing a null pointer is a no-op.
        #[no_mangle]
        pub unsafe extern "C" fn _strategy_gpt_drop(ptr: *mut ::core::ffi::c_void) {
            if ptr.is_null() {
                return;
            }
            // SAFETY: callers contract; pointer originated from `_strategy_gpt_create`.
            let _outer: ::std::boxed::Box<::std::boxed::Box<dyn $crate::Strategy>> = unsafe {
                ::std::boxed::Box::from_raw(ptr.cast::<::std::boxed::Box<dyn $crate::Strategy>>())
            };
        }

        /// Major ABI version this artifact was built against.
        #[no_mangle]
        pub extern "C" fn _strategy_gpt_abi_major() -> u16 {
            $crate::RUNNER_VERSION.major
        }
    };
}
