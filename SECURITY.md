# Security Policy

Image Batcher reads provider credentials from local environment variables or a local `.env` file. Credentials must never be committed to the repository.

If you find a security issue, please open a private GitHub security advisory or contact the maintainer privately before publishing details.

Before sharing logs or reproduction projects, remove:

- provider API keys;
- generated images that contain private data;
- local session files from `~/.imagen/`;
- exported prompt/image metadata from real users or customers.
