## 2026-08-22 - [Hashlib File Digest Optimization]
**Learning:** Python 3.11+ provides `hashlib.file_digest`, which allows calculating a hash over a file handle efficiently in C instead of doing manual chunked reading in Python loops. The repository targets Python 3.12 where it's available.
**Action:** Use `hashlib.file_digest` instead of standard hashing over chunk loops when computing hashes for file contents, and fallback using `hasattr` where backward compatibility is needed.
