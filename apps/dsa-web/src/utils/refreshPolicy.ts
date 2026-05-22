export const REFRESH_POLICY_MS = {
  /**
   * Realtime quotes for watched/discovery stocks. Used as a fallback poll;
   * focus/visibility observers trigger immediate refreshes with throttling.
   */
  realtimeQuoteFallback: 60_000,
  realtimeQuoteMinGap: 5_000,

  /** Open-market simulated positions need second-level mark-to-market updates. */
  livePosition: 5_000,

  /** Account, orders, signals and scheduler state are minute-level UI data. */
  tradingDashboard: 60_000,

  /** Async job polling should feel responsive without flooding the API. */
  jobStatus: 2_000,

  /** Scanner/gold-digger task progress is operational state, not market data. */
  taskProgress: 5_000,
} as const;
