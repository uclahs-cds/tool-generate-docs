# Build and Deploy Pipeline Documentation

This Github Action builds a documentation website and deploys it to [GitHub Pages](https://pages.github.com/) for the UCLAHS-CDS pipelines using [MKDocs](https://www.mkdocs.org/).

The pipeline's README.md is split into individual pages based on [level 2 headings](https://www.markdownguide.org/basic-syntax/#headings). A MkDocs config yaml file can also be used to specify specific parameters including additional documentation. Documentation must be written in Markdown syntax.

## Usage

```yaml
---
name: Generate Docs

on:
  workflow_dispatch:
  push:
    branches:
      - main
    tags:
      - 'v[0-9]*'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: uclahs-cds/tool-generate-docs@v1
```

## Parameters

Parameters can be specified using the [`with`](https://docs.github.com/en/actions/creating-actions/metadata-syntax-for-github-actions#runsstepswith) option.

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| `readme` | string | no | Relative path to the README file. Defaults to `'README.md'`. |
| `mkdocs_config` | string | no | Relative path to the MKDocs config yaml. Defaults to `'None'`. |

## Backfilling Documentation

This Action only creates documentation for new tags and commits. If you want to backfill documentation for older tags, you can do so with the [`backfill.py`](./internal-action/backfill.py) script:

```console
usage: backfill.py [-h] repo_url

positional arguments:
  repo_url

options:
  -h, --help  show this help message and exit
```

The script generates all documentation locally and serves it at <http://localhost:8000> for review. It then waits for user approval before pushing anything to GitHub.

```console
$ ./internal-action/backfill.py git@github.com:uclahs-cds/user-nwiltsie.git
Cloning repository into /var/folders/q5/pzb2r_1s01l6gvysk3cglxm4wpvxcb/T/tmpldl3gdxr ...
Generating docs for tag `v1.0.1`
Generating docs for tag `v1.0.2-rc.1`
Generating docs for tag `v1.0.2-rc.2`
Generating docs for tag `v1.0.2`
Generating docs for tag `v1.0.3`
Generating docs for tag `v1.0.4`
Updated documentation at http://localhost:8000/
Push these docs live [yes/no]?
```

## License

Author: Nicholas Wiltsie, Chenghao Zhu

tool-generate-docs is licensed under the GNU General Public License version 2. See the file LICENSE.md for the terms of the GNU GPL license.

Copyright (C) 2024 University of California Los Angeles ("Boutros Lab") All rights reserved.

This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
