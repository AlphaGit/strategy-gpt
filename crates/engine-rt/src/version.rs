use serde::{Deserialize, Serialize};
use std::fmt;

/// Semantic version of the strategy runtime ABI.
///
/// Strategies record the runner version they were built against. The runtime
/// loads only artifacts whose runner version major matches [`RUNNER_VERSION`];
/// older majors are flagged for migration (see spec `strategy-runtime`).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct RunnerVersion {
    pub major: u16,
    pub minor: u16,
    pub patch: u16,
}

impl RunnerVersion {
    pub const fn new(major: u16, minor: u16, patch: u16) -> Self {
        Self {
            major,
            minor,
            patch,
        }
    }

    /// Two versions share the same ABI when their major numbers match.
    pub const fn abi_compatible_with(self, other: Self) -> bool {
        self.major == other.major
    }
}

impl fmt::Display for RunnerVersion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}.{}", self.major, self.minor, self.patch)
    }
}

/// The runtime's current version. Bump major on any breaking change to the
/// `Strategy` trait or `Context` API.
pub const RUNNER_VERSION: RunnerVersion = RunnerVersion::new(0, 1, 0);
