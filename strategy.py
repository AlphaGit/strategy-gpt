strategy = """> This is a strategy that detects ranges on the volatility of the VXX ETF.
> It shorts the action when possible, and keeps the position open until the relative profit is below 10%.
> It never shorts so much that the risk of loss goes beyond 25%.
> It hedges using treasury bonds for the unused capital."""

parameters = """- Position size: 20% of the capital.
- Volatility range for entries: 10% to 20%.
- Relative profit for exits: 10%.
- Maximum risk of loss: 25%.
- Hedging instrument: US treasury bonds.
- Hedging ratio: 1:5."""

metrics = """- Sharpe ratio: 0.9.
- Sortino ratio: 0.9.
- Trade length: 10 days.
- Maximum drawdown: 10%.
- Annualized return: 9%.
- Profit factor: 1.09.
- Win ratio: 35%."""
