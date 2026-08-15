# Declarative Kubernetes Source

This directory is reserved for checked-in Kubernetes source of truth.

The scaffold phase keeps runtime behavior safe:

- example configuration is validated offline;
- render output is generated into ignored working directories;
- no generated runtime resource is manually maintained here;
- no `kubectl` command is run by the scaffold.

Future implementation phases should add baseline manifests under versioned directories and keep generated controller resources out of this tree.

