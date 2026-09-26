# Contributing

Use Python 3.14 and Node.js 24 for the reference development environment. Follow the build and test commands in the README. Keep HTTP handling, AiiDA orchestration and numerical analysis in their respective packages.

Changes to a public API require an accompanying API test and a coordinated change in the browser client. Test rejected requests as well as successful operations. Scientific acceptance must remain distinct from process exit status. Avoid browser persistence of access tokens and do not put credentials in example files.

The browser bundle is generated during the build. Commit source files and `frontend/package-lock.json`, not generated assets or dependency directories. The root integration repository records component Git submodules and a generated Python workspace lock. Update dependency versions in package metadata and regenerate that central lock together; do not duplicate component revision pins or Python lockfiles here.

Open issues and pull requests at https://github.com/AfonenkoA/qcl-negf-portal. Include a minimal reproducible case with synthetic or publicly shareable input data. Remove credentials, usernames, infrastructure addresses and unpublished scientific data from logs before sharing them.

Integration CI is defined in the [qcl-negf superproject](https://github.com/AfonenkoA/qcl-negf) and uses its local runner. Update the component gitlink there to check a change with the complete selected source graph.
