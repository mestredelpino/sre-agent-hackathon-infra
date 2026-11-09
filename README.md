# SRE Agent Hackathon Infrastructure

Authoritative local Terraform deployment for an isolated Azure SRE hackathon environment. Deployment identifiers and region are supplied through ignored local variable files. A compatibility symlink from the previous local directory points here; it is not a second state directory.

Terraform source, provider lock file and the baseline workload can be version controlled. Local state, backups, saved plans, kubeconfig, variable files and identity-recovery material are deliberately ignored. Ignored does not mean encrypted: protect this directory with Windows filesystem permissions and do not publish its local artifacts. No Git remote or commit is created automatically.

Run Terraform from this directory using the existing state and local `*.auto.tfvars` files, including the resource-group name input. Never initialize a second deployment against the same Azure names. Review a new plan before any apply; saved plans from before relocation are historical artifacts and should not be applied. Do not copy generic workshop Terraform over these deployed overrides without reviewing resource addresses and networking changes.

The workshop source and store/dashboard helpers are in the sibling `../sre-agent-hackathon` directory. Pending agent access and store/dashboard setup changes still require reconciliation into this deployment and a reviewed plan. The subnet HTTP repair is now deployed through `store-network.tf`; see [recovery verification and remaining data/alerting gaps](RECOVERY.md). The relocation itself did not apply Azure changes.

```bash
terraform validate
terraform plan -input=false
```

The kubeconfig remains local at `./kubeconfig`. Existing processes using the old path continue through the symlink. Coordinate all edits and state operations with other agents/operators; the directory must have only one authoritative state.

## Existing Resource Group

Terraform 1.7 or newer is required. The resource group must already exist in the configured subscription. `data.azurerm_resource_group.main` reads its name, location and ID using the local `resource_group_name` input; this configuration does not create the group or manage its tags. Keep the local `location` input consistent with the existing environment because the storefront rule uses it to identify an externally managed NSG.

For state created by the previous configuration, the `removed` block declares that `azurerm_resource_group.main` must leave Terraform management without being destroyed. No manual `terraform state rm` is needed. Back up the authoritative state securely, generate a fresh plan, and verify that the group is reported as no longer managed rather than destroyed. Review all other changes before applying; the migration does not automatically approve unrelated drift. The state change takes effect only after a reviewed apply, and the group remains outside Terraform ownership afterward, including during destroy.

For a new state, the data source looks up the existing group; it does not import or adopt existing child resources. Restore the authoritative state or explicitly import those resources before planning against an existing deployment.

## Git and Local Files

Commit the Terraform source, `.terraform.lock.hcl`, `.gitignore`, workload manifest, documentation and `terraform.tfvars.example`. The example contains placeholders only; it is not loaded automatically. Never overwrite the existing local variable files with it.

Saved `*.tfplan` files and their JSON exports are disposable and ignored. Generate a fresh plan whenever needed. Keep local state, state backups, variable files, kubeconfig and `identity-recovery/` out of Git and preserve them for deployment management and recovery. The `.terraform/` directory is a disposable provider cache; `terraform init` can recreate it using the committed lock file.

A fresh clone does not contain the deployed environment's state or inputs. Restore the authoritative state and approved inputs securely before planning against existing Azure resources; do not apply from an empty state. This configuration includes deployment-specific overrides and is not a generic new-environment template.

Before the first commit, validate and review exactly what Git will include:

```bash
terraform fmt -check
terraform validate
git add .
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Ignored files are not encrypted or backed up by Git. Keep secure backups of deployment state and recovery material separately.# sre-agent-hackathon-infra
