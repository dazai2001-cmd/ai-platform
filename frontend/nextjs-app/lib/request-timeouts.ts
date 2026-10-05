export const REQUEST_TIMEOUT_MS = 180_000;

// Retrieval, cold starts, and model loading happen before the first answer.
// Once output begins, a shorter timeout catches a stream that has stalled.
export const STREAM_START_TIMEOUT_MS = REQUEST_TIMEOUT_MS;
export const STREAM_INACTIVITY_TIMEOUT_MS = 30_000;
