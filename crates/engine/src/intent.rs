//! Engine-side tracking of submitted trade intents.
//!
//! Strategies do not see this state. The intent book is engine-internal: it
//! records pending market and limit intents, applies the configured fill
//! model on each new bar, and emits fills.

use chrono::{DateTime, Utc};
use engine_rt::{Bar, Fill, Order, OrderId};
use serde::{Deserialize, Serialize};

use crate::fill_model::FillModel;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentStatus {
    Pending,
    Filled,
    Expired,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PendingIntent {
    pub order: Order,
    pub status: IntentStatus,
    /// Bar timestamp at which the intent was submitted. Used by `NextBarOpen`
    /// to skip the same-bar fill.
    pub submitted_on_bar: DateTime<Utc>,
}

#[derive(Clone, Debug, Default)]
pub struct IntentBook {
    intents: Vec<PendingIntent>,
    next_id: u64,
}

impl IntentBook {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn submit(&mut self, mut order: Order, submitted_on_bar: DateTime<Utc>) -> OrderId {
        self.next_id += 1;
        order.id = OrderId(self.next_id);
        order.submitted_at = submitted_on_bar;
        let id = order.id;
        self.intents.push(PendingIntent {
            order,
            status: IntentStatus::Pending,
            submitted_on_bar,
        });
        id
    }

    pub fn pending(&self) -> impl Iterator<Item = &PendingIntent> {
        self.intents
            .iter()
            .filter(|i| matches!(i.status, IntentStatus::Pending))
    }

    pub fn count_pending(&self) -> usize {
        self.intents
            .iter()
            .filter(|i| matches!(i.status, IntentStatus::Pending))
            .count()
    }

    /// Try to fill every pending intent against `bar`, using `model` and the
    /// `fee_fn` for each potential fill. Returns the produced fills in
    /// submission order. The matching intent's status flips to `Filled`.
    pub fn try_fill(
        &mut self,
        bar: &Bar,
        model: FillModel,
        mut fee_fn: impl FnMut(&Order, f64) -> f64,
    ) -> Vec<Fill> {
        let mut fills = Vec::new();
        for intent in self.intents.iter_mut() {
            if intent.status != IntentStatus::Pending {
                continue;
            }
            // NextBarOpen never fills on the bar of submission.
            if model == FillModel::NextBarOpen && intent.submitted_on_bar == bar.ts {
                continue;
            }
            let price = if intent.order.limit_price.is_some() {
                model.limit_fill_price(&intent.order, bar)
            } else {
                Some(model.market_fill_price(bar))
            };
            if let Some(p) = price {
                let fee = fee_fn(&intent.order, p);
                fills.push(FillModel::make_fill(&intent.order, p, bar, fee));
                intent.status = IntentStatus::Filled;
            }
        }
        fills
    }

    /// Mark every still-pending intent as expired. Useful at end-of-run.
    pub fn expire_all_pending(&mut self) {
        for intent in self.intents.iter_mut() {
            if intent.status == IntentStatus::Pending {
                intent.status = IntentStatus::Expired;
            }
        }
    }
}
