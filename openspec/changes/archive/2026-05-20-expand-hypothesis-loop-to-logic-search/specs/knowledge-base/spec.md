## ADDED Requirements

### Requirement: Prior-decision-aware retrieval filter

The Knowledge Base SHALL expose a retrieval pipeline that lets a client apply a deterministic post-retrieval filter consuming a list of prior decisions. The filter MUST be able to (a) drop or score-discount chunks whose `(source, locator)` appears in the `kb_cites` of any rejected prior decision, and (b) boost chunks whose `(source, locator)` appears in the `kb_cites` of any accepted prior decision. The filter MUST be implemented client-side over the KB's standard retrieval results; the KB client interface MUST NOT require knowledge of decision history.

#### Scenario: Recycled rejected chunk is suppressed

- **WHEN** a client retrieves with a prior-decision list whose rejected entries cite the chunk at `(book_x, p.42)`
- **THEN** the filter drops or score-discounts that chunk before returning the result set

#### Scenario: Accepted-cite chunk is uplifted

- **WHEN** a client retrieves with a prior-decision list whose accepted entries cite the chunk at `(paper_y, sec.3)`
- **THEN** the filter raises that chunk's effective score so it ranks higher in the returned set

#### Scenario: KB client remains decision-agnostic

- **WHEN** the KB's retrieve API is called without a prior-decision list
- **THEN** the KB returns the standard score-ranked top-k result set with no filter applied
