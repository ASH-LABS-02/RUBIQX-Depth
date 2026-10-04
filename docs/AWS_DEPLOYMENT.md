# AWS deployment

Use the existing EC2 instance where possible. Provisioning a new instance requires
an agreed region and budget. CPU inference is supported; actual latency depends
on instance resources and scene size. Check free disk and memory before building.

## Required access

- EC2 host, SSH username and local private-key path, or authenticated AWS access.
- A domain with DNS pointing to the server for the production Caddy configuration.
- TCP 80/443 reachable; SSH restricted to the administrator's address. Do not
  expose the application's internal port 8000 publicly.
- Docker Engine and Compose on the server.
- The approved v6a checkpoint directory (approximately 390 MB of weights), with
  config and processor assets. Semantic experimental weights are not required.

## Deploy a pinned source revision

Use a dedicated checkout on the server. Transfer source from the local checkout
or clone with an authorised private-repository credential. Never put GitHub tokens
in commands, images or committed files. Check out the intended commit before build.

Place the checkpoint in `models/da2-gamus-full`. Transfer the complete directory,
not only `model.safetensors`. Transfer selected saved gallery jobs into `data/jobs/`
if they are not already present. Do not overwrite existing user jobs.

Create an untracked `.env.production` on the server containing:

```dotenv
DEPTHWIZARD_DOMAIN=your-domain.example
DEPTHWIZARD_AUTH_USER=your-user
DEPTHWIZARD_AUTH_PASSWORD=replace-with-a-strong-unique-password
DEPTHWIZARD_BUILD_SHA=full-deployed-git-commit
```

Restrict the file to the deployment owner (`chmod 600 .env.production`). Substitute
real values before starting. Do not commit or share this file.

From the server checkout:

```sh
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml ps
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail 100 depthwizard caddy
```

The initial build requires network access for dependencies and pretrained Small
weights. Production mounts the fine-tuned checkpoint read-only. Caddy stores its
certificates in persistent volumes. Back up user data and the previous deployed
revision before replacing an existing deployment.

## Acceptance and rollback

Check HTTPS and login in the browser, open a saved City scene, and confirm the
model is ready before attempting an upload. Verify `/api/health` reports the
intended commit/model identity. Check `/api/scenes`, job processing and an export.
Do not claim live availability until these checks pass from outside the server.

On failure, retain logs, restore the previous pinned source/configuration and
rebuild that revision. Preserve the data and Caddy volumes; do not use `down -v`.
Use [release readiness](release-readiness.md) for the remaining evidence checks.

No deployment is performed merely by creating this guide. AWS/SSH access and a
production domain must be supplied before the live service can be updated.

## Deployed 4 October 2026

- HTTPS: https://13-62-80-223.sslip.io
- Elastic IP: 13.62.80.223; Stockholm eu-north-1; m7i-flex.large CPU instance.
- Source revision: 7cb32cffa8fa788ced2923e03a80034ba9012383.
- v6a checkpoint SHA256: 3f8d8835ad8082adb0050db689484185ab1787f617c0221ef0a9b07c74146272.
- Caddy HTTPS, mandatory application authentication and persistent data/model mounts.
- CPU override: docker-compose.cpu-host.yml; 8 MP input cap, two compute threads.
- External checks: HTTPS health, authenticated root/scenes/local-model requests,
  Glover viewer metadata/texture, Bengaluru heights and trees.js returned 200.
- 12 saved scenes; Docker application healthcheck passed. A local-only checkpoint
  CPU forward pass returned finite values (518 × 518); this was not an accuracy
  or full-upload latency benchmark. Browser interaction and full upload/export
  acceptance on this host remain pending.
- Credentials stored outside the repository and excluded from Docker build context.
  Do not publish the server environment file.

For this host, append `-f docker-compose.cpu-host.yml` to the production Compose
commands above. The temporary sslip.io address can later be replaced with a custom
DNS name by updating DEPTHWIZARD_DOMAIN and restarting Caddy.
