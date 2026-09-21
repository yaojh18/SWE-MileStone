use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// A cow-like type for static vs owned values, used for completion lists
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NuCow<B, O> {
    Borrowed(B),
    Owned(O),
}

// Specific implementation for the only use case in this codebase
impl Serialize for NuCow<&'static [&'static str], Vec<String>> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            NuCow::Borrowed(b) => {
                let owned: Vec<String> = b.iter().map(|s| s.to_string()).collect();
                owned.serialize(serializer)
            }
            NuCow::Owned(o) => o.serialize(serializer),
        }
    }
}

impl<'de> Deserialize<'de> for NuCow<&'static [&'static str], Vec<String>> {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let owned = Vec::<String>::deserialize(deserializer)?;
        Ok(NuCow::Owned(owned))
    }
}
