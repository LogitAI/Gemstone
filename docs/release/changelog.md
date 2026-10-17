# Changelog

Changes that a user of the server or the client can notice, newest first. Each entry links the
issue that records the decision.

## Unreleased (1.1.0)

### Changed

- **Seeded sampling on the exclusive path gives different text, once.** The engine's exclusive
  path now samples with the same per-request sampler as continuous batching instead of
  `torch.manual_seed` and `generate`'s own sampling ([#182](https://github.com/LogitAI/Gemstone/issues/182)).
  A seed now gives the same tokens whether or not the request is batched, and a seeded reply no
  longer changes when another model samples at the same time. The cost: a seeded request that
  takes the exclusive path (it sets `typical_p` or `repeat_penalty`, passes extra `generate`
  arguments, runs with batching off, or asks for more tokens than the KV cache holds) returns
  different text than the same seed returned in 1.0.x. Seeded batched requests are unchanged.

### Fixed

- Two simultaneous deletes of one session returned 500 instead of 404, and an expiry sweep could
  fail while another request created a session ([#180](https://github.com/LogitAI/Gemstone/issues/180)).
