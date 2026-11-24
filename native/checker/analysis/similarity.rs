//! `difflib.SequenceMatcher.ratio` and `difflib.get_close_matches` over characters.
use std::collections::HashMap;

/// The share of matching characters, `2 * matches / (len(a) + len(b))`.
pub fn ratio(a: &str, b: &str) -> f64 {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let total = a.len() + b.len();
    if total == 0 {
        return 1.0;
    }
    let matches = Matcher::new(&a, &b).matching_characters();
    // Lengths of names are far below 2^52, so the conversion is exact.
    #[allow(clippy::cast_precision_loss)]
    let share = 2.0 * matches as f64 / total as f64;
    share
}

/// The best `n` possibilities scoring at least `cutoff` against `word`, best first.
pub fn close_matches<'p>(
    word: &str,
    possibilities: &[&'p str],
    n: usize,
    cutoff: f64,
) -> Vec<&'p str> {
    let mut scored: Vec<(f64, &str)> = possibilities
        .iter()
        .map(|candidate| (ratio(candidate, word), *candidate))
        .filter(|(score, _)| *score >= cutoff)
        .collect();
    // `heapq.nlargest` over `(score, candidate)` tuples.
    scored.sort_by(|left, right| right.0.total_cmp(&left.0).then_with(|| right.1.cmp(left.1)));
    scored
        .into_iter()
        .take(n)
        .map(|(_, candidate)| candidate)
        .collect()
}

struct Matcher<'s> {
    a: &'s [char],
    b: &'s [char],
    b2j: HashMap<char, Vec<usize>>,
}

impl<'s> Matcher<'s> {
    fn new(a: &'s [char], b: &'s [char]) -> Self {
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (index, item) in b.iter().enumerate() {
            b2j.entry(*item).or_default().push(index);
        }
        // CPython's autojunk heuristic drops very frequent elements of long sequences.
        if b.len() >= 200 {
            let limit = b.len() / 100 + 1;
            b2j.retain(|_, indices| indices.len() <= limit);
        }
        Self { a, b, b2j }
    }

    fn longest_match(
        &self,
        alo: usize,
        ahi: usize,
        blo: usize,
        bhi: usize,
    ) -> (usize, usize, usize) {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            let mut next: HashMap<usize, usize> = HashMap::new();
            if let Some(indices) = self.b2j.get(&self.a[i]) {
                for &j in indices {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = j
                        .checked_sub(1)
                        .and_then(|previous| j2len.get(&previous))
                        .copied()
                        .unwrap_or(0)
                        + 1;
                    next.insert(j, k);
                    if k > bestsize {
                        (besti, bestj, bestsize) = (i + 1 - k, j + 1 - k, k);
                    }
                }
            }
            j2len = next;
        }
        while besti > alo && bestj > blo && self.a[besti - 1] == self.b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && self.a[besti + bestsize] == self.b[bestj + bestsize]
        {
            bestsize += 1;
        }
        (besti, bestj, bestsize)
    }

    fn matching_characters(&self) -> usize {
        let mut queue = vec![(0, self.a.len(), 0, self.b.len())];
        let mut total = 0;
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let (i, j, k) = self.longest_match(alo, ahi, blo, bhi);
            if k == 0 {
                continue;
            }
            total += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
        total
    }
}

#[cfg(test)]
mod tests {
    use super::{close_matches, ratio};

    #[test]
    fn matches_difflib_ratios() {
        assert!((ratio("abcd", "bcde") - 0.75).abs() < 1e-12);
        assert!((ratio("invoise", "invoice") - 6.0 / 7.0).abs() < 1e-12);
        assert!((ratio("", "") - 1.0).abs() < 1e-12);
    }

    #[test]
    fn picks_the_closest_match() {
        let names = ["errors", "invoice", "memory"];
        assert_eq!(close_matches("invoise", &names, 1, 0.85), vec!["invoice"]);
        assert!(close_matches("memoir", &names, 1, 0.85).is_empty());
    }
}
