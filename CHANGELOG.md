# Changelog

## 1.0.11

- protected provider API keys with Windows DPAPI and migrated legacy plaintext profiles;
- prevented Excel formula injection and destructive report replacement;
- added strict finite numeric validation while preserving `без НДС`;
- added EXIF orientation, BMP/TIFF conversion and natural page sorting;
- hardened OpenAI-compatible response validation, retry policy and secret redaction;
- made provider connection checks non-destructive and preserved drafts during language changes;
- required HTTPS for remote providers and fixed full endpoints with query parameters;
- verified update assets with GitHub SHA-256 digests and added updater backup/rollback;
- restored portable first-run configuration through a CLI wizard;
- added regular CI, pinned dependencies/actions and separated release permissions.
- fixed GitHub Actions release asset staging for current artifact upload rules.
- explicitly disabled streaming for provider checks and document processing;
- added safe Content-Type diagnostics for malformed provider responses.
- preserved provider profiles and application settings during local EXE rebuilds.
