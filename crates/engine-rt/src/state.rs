use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct StateKey(pub String);

impl<S: Into<String>> From<S> for StateKey {
    fn from(s: S) -> Self {
        Self(s.into())
    }
}
