//! Graph store (v1: SQLite-backed; future: kuzu crate).

use rusqlite::params;

use crate::error::KbError;
use crate::schema::{NodeKind, NodeRecord, RelationRecord};
use crate::store::Store;

pub trait GraphStore {
    fn upsert_source_node(&mut self, source_id: &str, title: &str) -> Result<(), KbError>;
    fn insert_node(&mut self, node: &NodeRecord) -> Result<(), KbError>;
    fn insert_edge(&mut self, edge: &RelationRecord) -> Result<(), KbError>;
    fn get_node(&self, id: &str) -> Result<Option<NodeRecord>, KbError>;
    fn nodes_for_source(&self, source_id: &str) -> Result<Vec<NodeRecord>, KbError>;
    /// Return all neighbours within `hops` of `seed_ids`. Always includes the
    /// seeds themselves in the result.
    fn neighborhood(&self, seed_ids: &[String], hops: usize) -> Result<Vec<NodeRecord>, KbError>;
}

fn row_to_node(row: &rusqlite::Row<'_>) -> rusqlite::Result<NodeRecord> {
    let kind_str: String = row.get(1)?;
    let kind = NodeKind::parse(&kind_str).map_err(|e| {
        rusqlite::Error::FromSqlConversionFailure(1, rusqlite::types::Type::Text, Box::new(e))
    })?;
    let data_json: String = row.get(5)?;
    let data: serde_json::Value = serde_json::from_str(&data_json).map_err(|e| {
        rusqlite::Error::FromSqlConversionFailure(5, rusqlite::types::Type::Text, Box::new(e))
    })?;
    Ok(NodeRecord {
        id: row.get(0)?,
        kind,
        name: row.get(2)?,
        summary: row.get(3)?,
        source_id: row.get(4)?,
        data,
    })
}

impl GraphStore for Store {
    fn upsert_source_node(&mut self, source_id: &str, title: &str) -> Result<(), KbError> {
        // Source nodes use the same id as the source row so provenance lookups
        // are O(1).
        self.conn.execute(
            "INSERT INTO nodes (id, kind, name, summary, source_id, data_json) \
             VALUES (?1, 'Source', ?2, '', NULL, '{}') \
             ON CONFLICT(id) DO UPDATE SET name = excluded.name",
            params![source_id, title],
        )?;
        Ok(())
    }

    fn insert_node(&mut self, node: &NodeRecord) -> Result<(), KbError> {
        if node.kind != NodeKind::Source && node.source_id.is_none() {
            return Err(KbError::Config(format!(
                "node {} ({}) is missing required source_id provenance",
                node.id,
                node.kind.as_str()
            )));
        }
        let data_json = serde_json::to_string(&node.data)?;
        self.conn.execute(
            "INSERT INTO nodes (id, kind, name, summary, source_id, data_json) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
             ON CONFLICT(id) DO UPDATE SET \
                kind = excluded.kind, \
                name = excluded.name, \
                summary = excluded.summary, \
                source_id = excluded.source_id, \
                data_json = excluded.data_json",
            params![
                node.id,
                node.kind.as_str(),
                node.name,
                node.summary,
                node.source_id,
                data_json,
            ],
        )?;
        Ok(())
    }

    fn insert_edge(&mut self, edge: &RelationRecord) -> Result<(), KbError> {
        self.conn.execute(
            "INSERT INTO edges (src_id, dst_id, kind, weight, evidence_chunk_id) \
             VALUES (?1, ?2, ?3, ?4, ?5) \
             ON CONFLICT(src_id, dst_id, kind) DO UPDATE SET \
                weight = excluded.weight, \
                evidence_chunk_id = excluded.evidence_chunk_id",
            params![
                edge.src_id,
                edge.dst_id,
                edge.kind.as_str(),
                edge.weight as f64,
                edge.evidence_chunk_id,
            ],
        )?;
        Ok(())
    }

    fn get_node(&self, id: &str) -> Result<Option<NodeRecord>, KbError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, kind, name, summary, source_id, data_json FROM nodes WHERE id = ?1",
        )?;
        let mut rows = stmt.query(params![id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(row_to_node(row)?))
        } else {
            Ok(None)
        }
    }

    fn nodes_for_source(&self, source_id: &str) -> Result<Vec<NodeRecord>, KbError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, kind, name, summary, source_id, data_json FROM nodes WHERE source_id = ?1",
        )?;
        let rows = stmt.query_map(params![source_id], row_to_node)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    fn neighborhood(&self, seed_ids: &[String], hops: usize) -> Result<Vec<NodeRecord>, KbError> {
        use std::collections::HashSet;
        let mut frontier: HashSet<String> = seed_ids.iter().cloned().collect();
        let mut all: HashSet<String> = frontier.clone();
        for _ in 0..hops {
            if frontier.is_empty() {
                break;
            }
            let placeholders = frontier.iter().map(|_| "?").collect::<Vec<_>>().join(",");
            let sql = format!(
                "SELECT DISTINCT n.id FROM nodes n \
                 JOIN edges e ON (e.dst_id = n.id AND e.src_id IN ({0})) \
                              OR (e.src_id = n.id AND e.dst_id IN ({0}))",
                placeholders
            );
            let mut stmt = self.conn.prepare(&sql)?;
            // SQL references the same placeholder list twice; pass values twice.
            let frontier_vec: Vec<&String> = frontier.iter().collect();
            let mut params_owned: Vec<&dyn rusqlite::ToSql> = Vec::new();
            for s in &frontier_vec {
                params_owned.push(*s as &dyn rusqlite::ToSql);
            }
            for s in &frontier_vec {
                params_owned.push(*s as &dyn rusqlite::ToSql);
            }
            let rows = stmt.query_map(params_owned.as_slice(), |row| {
                let id: String = row.get(0)?;
                Ok(id)
            })?;
            let mut next: HashSet<String> = HashSet::new();
            for r in rows {
                let id = r?;
                if all.insert(id.clone()) {
                    next.insert(id);
                }
            }
            frontier = next;
        }
        let ids: Vec<String> = all.into_iter().collect();
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let placeholders = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT id, kind, name, summary, source_id, data_json FROM nodes WHERE id IN ({})",
            placeholders
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let params_owned: Vec<&dyn rusqlite::ToSql> =
            ids.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
        let rows = stmt.query_map(params_owned.as_slice(), row_to_node)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::EdgeKind;

    fn n(id: &str, kind: NodeKind, source: Option<&str>) -> NodeRecord {
        NodeRecord {
            id: id.to_string(),
            kind,
            name: id.to_string(),
            summary: String::new(),
            source_id: source.map(|s| s.to_string()),
            data: serde_json::json!({}),
        }
    }

    fn e(src: &str, dst: &str, kind: EdgeKind) -> RelationRecord {
        RelationRecord {
            src_id: src.to_string(),
            dst_id: dst.to_string(),
            kind,
            weight: 1.0,
            evidence_chunk_id: None,
        }
    }

    #[test]
    fn rejects_node_without_provenance() {
        let mut store = Store::open_in_memory().unwrap();
        let node = n("vix", NodeKind::Indicator, None);
        assert!(store.insert_node(&node).is_err());
    }

    #[test]
    fn neighborhood_walks_edges() {
        let mut store = Store::open_in_memory().unwrap();
        store
            .conn
            .execute(
                "INSERT INTO sources (id, kind, title, path, ingested_at, content_hash) \
                 VALUES ('s1', 'note', 't', 'p', '2026-01-01T00:00:00Z', 'h')",
                [],
            )
            .unwrap();
        store.upsert_source_node("s1", "Source One").unwrap();
        store
            .insert_node(&n("a", NodeKind::Concept, Some("s1")))
            .unwrap();
        store
            .insert_node(&n("b", NodeKind::Concept, Some("s1")))
            .unwrap();
        store
            .insert_node(&n("c", NodeKind::Concept, Some("s1")))
            .unwrap();
        store
            .insert_edge(&e("a", "b", EdgeKind::Implements))
            .unwrap();
        store.insert_edge(&e("b", "c", EdgeKind::Requires)).unwrap();

        let one_hop = store.neighborhood(&["a".to_string()], 1).unwrap();
        let ids: std::collections::HashSet<_> = one_hop.iter().map(|n| n.id.clone()).collect();
        assert!(ids.contains("a"));
        assert!(ids.contains("b"));
        assert!(!ids.contains("c"));

        let two_hop = store.neighborhood(&["a".to_string()], 2).unwrap();
        let ids: std::collections::HashSet<_> = two_hop.iter().map(|n| n.id.clone()).collect();
        assert!(ids.contains("c"));
    }
}
