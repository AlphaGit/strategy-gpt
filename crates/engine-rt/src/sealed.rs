/// Private supertrait to seal the [`crate::Strategy`] trait so external crates
/// cannot implement it without going through this runtime.
pub trait Sealed {}
