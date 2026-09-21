use serde::{Deserialize, Serialize};

/// A Cow-like enum for static or owned values, used for completion lists.
/// This is an ENV-PATCH to provide a type that was expected by some cherry-picked commits.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum NuCow<B: 'static, O> {
    /// Borrowed static reference
    Borrowed(B),
    /// Owned value
    Owned(O),
}
