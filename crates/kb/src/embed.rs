//! Embedder abstraction. Production embedder is configured by the orchestrator
//! (OpenAI / Anthropic / local model); tests use [`HashEmbedder`] which is
//! deterministic, dependency-free, and never touches the network.

use crate::error::KbError;

pub trait Embedder: Send + Sync {
    fn dim(&self) -> usize;
    fn embed(&self, text: &str) -> Result<Vec<f32>, KbError>;
    fn embed_batch(&self, texts: &[&str]) -> Result<Vec<Vec<f32>>, KbError> {
        let mut out = Vec::with_capacity(texts.len());
        for t in texts {
            out.push(self.embed(t)?);
        }
        Ok(out)
    }
}

/// Deterministic content-addressed bag-of-words style embedder.
///
/// Tokenises lowercased ASCII alphabetic words and accumulates their
/// blake3-hashed projection into a fixed-dim vector. Cosine similarity over
/// these vectors recovers a sensible nearest-neighbour ordering for keyword
/// queries — enough to drive the KB's retrieval tests without an LLM.
#[derive(Debug, Clone)]
pub struct HashEmbedder {
    dim: usize,
}

impl HashEmbedder {
    pub fn new(dim: usize) -> Self {
        assert!(dim > 0, "dim must be positive");
        Self { dim }
    }
}

impl Default for HashEmbedder {
    fn default() -> Self {
        Self::new(64)
    }
}

impl Embedder for HashEmbedder {
    fn dim(&self) -> usize {
        self.dim
    }

    fn embed(&self, text: &str) -> Result<Vec<f32>, KbError> {
        let mut vec = vec![0.0_f32; self.dim];
        for token in tokenise(text) {
            let hash = blake3::hash(token.as_bytes());
            let bytes = hash.as_bytes();
            // Use first 8 bytes as a u64 to derive index + sign; gives stable
            // distribution across the vector dims.
            let primary = u64::from_le_bytes([
                bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            ]);
            let idx = (primary % self.dim as u64) as usize;
            let sign = if (bytes[8] & 1) == 0 { 1.0 } else { -1.0 };
            vec[idx] += sign;
        }
        let norm = vec.iter().map(|v| v * v).sum::<f32>().sqrt();
        if norm > 0.0 {
            for v in &mut vec {
                *v /= norm;
            }
        }
        Ok(vec)
    }
}

fn tokenise(text: &str) -> impl Iterator<Item = String> + '_ {
    text.split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedder_is_deterministic() {
        let e = HashEmbedder::new(16);
        let a = e.embed("volatility regime shifts").unwrap();
        let b = e.embed("volatility regime shifts").unwrap();
        assert_eq!(a, b);
        assert_eq!(a.len(), 16);
    }

    #[test]
    fn similar_text_has_higher_cosine() {
        let e = HashEmbedder::new(64);
        let target = e.embed("vix term structure backwardation").unwrap();
        let similar = e.embed("vix term structure inversion").unwrap();
        let unrelated = e.embed("ema crossover momentum").unwrap();
        let cos = |a: &[f32], b: &[f32]| a.iter().zip(b).map(|(x, y)| x * y).sum::<f32>();
        assert!(cos(&target, &similar) > cos(&target, &unrelated));
    }
}
