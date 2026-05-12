//! Whitespace-respecting chunker.
//!
//! Splits text into approximately `chunk_size`-character windows with
//! `overlap`-character carry-over between windows. Boundaries snap to
//! whitespace to avoid mid-word cuts. Pure function, no external deps.

#[derive(Debug, Clone)]
pub struct Chunk {
    pub text: String,
    pub ord: usize,
}

pub fn chunk_text(text: &str, chunk_size: usize, overlap: usize) -> Vec<Chunk> {
    assert!(overlap < chunk_size, "overlap must be < chunk_size");
    if text.is_empty() {
        return Vec::new();
    }
    let bytes = text.as_bytes();
    let len = bytes.len();
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut ord = 0usize;
    while start < len {
        let mut end = (start + chunk_size).min(len);
        if end < len {
            // Walk back to last whitespace to avoid splitting words.
            let mut probe = end;
            while probe > start && !bytes[probe].is_ascii_whitespace() {
                probe -= 1;
            }
            if probe > start {
                end = probe;
            }
        }
        // Ensure we slice on a UTF-8 boundary.
        while end > start && !text.is_char_boundary(end) {
            end -= 1;
        }
        let slice = text[start..end].trim();
        if !slice.is_empty() {
            out.push(Chunk {
                text: slice.to_string(),
                ord,
            });
            ord += 1;
        }
        if end == len {
            break;
        }
        let advance = chunk_size.saturating_sub(overlap).max(1);
        let next = start + advance;
        let next = next.min(end);
        start = if next <= start { end } else { next };
        while start < len && !text.is_char_boundary(start) {
            start += 1;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_long_text_into_windows() {
        let text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu";
        let chunks = chunk_text(text, 20, 5);
        assert!(chunks.len() > 1);
        for c in &chunks {
            assert!(c.text.len() <= 20);
        }
        // ords are dense and start at 0
        for (i, c) in chunks.iter().enumerate() {
            assert_eq!(c.ord, i);
        }
    }

    #[test]
    fn short_text_yields_one_chunk() {
        let chunks = chunk_text("hello world", 100, 10);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].text, "hello world");
    }

    #[test]
    fn empty_text_yields_no_chunks() {
        assert!(chunk_text("", 100, 10).is_empty());
    }
}
