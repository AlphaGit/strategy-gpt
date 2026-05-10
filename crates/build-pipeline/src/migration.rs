//! Runner-version migration decisions.
//!
//! Real migration (regenerating source via the LLM and rebuilding) lives
//! upstream in the orchestrator. This module decides *whether* migration is
//! required given an artifact's recorded runner version and the current one.

use engine_rt::RunnerVersion;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MigrationDecision {
    /// Artifact is current; reuse as-is.
    UpToDate,
    /// Same major; minor/patch newer in the runner. Compatible — reuse.
    CompatibleMinorOrPatch,
    /// Major mismatch. Artifact must be regenerated against the new ABI.
    MustMigrate,
    /// Artifact is built against a NEWER major than the current runtime.
    /// This is unexpected — the operator must downgrade the artifact or
    /// upgrade the runner.
    ArtifactAheadOfRunner,
}

pub fn migration_decision(artifact: RunnerVersion, current: RunnerVersion) -> MigrationDecision {
    if artifact == current {
        return MigrationDecision::UpToDate;
    }
    if artifact.major == current.major {
        if (artifact.minor, artifact.patch) <= (current.minor, current.patch) {
            return MigrationDecision::CompatibleMinorOrPatch;
        }
        return MigrationDecision::ArtifactAheadOfRunner;
    }
    if artifact.major < current.major {
        return MigrationDecision::MustMigrate;
    }
    MigrationDecision::ArtifactAheadOfRunner
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(maj: u16, min: u16, p: u16) -> RunnerVersion {
        RunnerVersion::new(maj, min, p)
    }

    #[test]
    fn equal_is_up_to_date() {
        assert_eq!(
            migration_decision(v(0, 1, 0), v(0, 1, 0)),
            MigrationDecision::UpToDate
        );
    }

    #[test]
    fn older_minor_same_major_is_compatible() {
        assert_eq!(
            migration_decision(v(1, 2, 3), v(1, 4, 0)),
            MigrationDecision::CompatibleMinorOrPatch
        );
    }

    #[test]
    fn older_major_must_migrate() {
        assert_eq!(
            migration_decision(v(1, 5, 5), v(2, 0, 0)),
            MigrationDecision::MustMigrate
        );
    }

    #[test]
    fn newer_major_artifact_ahead() {
        assert_eq!(
            migration_decision(v(3, 0, 0), v(2, 0, 0)),
            MigrationDecision::ArtifactAheadOfRunner
        );
    }

    #[test]
    fn newer_minor_same_major_artifact_ahead() {
        assert_eq!(
            migration_decision(v(1, 5, 0), v(1, 4, 0)),
            MigrationDecision::ArtifactAheadOfRunner
        );
    }
}
