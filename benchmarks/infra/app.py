"""CDK app for the benchmarking bucket, run by the ``cdk`` CLI (see cdk.json, README.md).

The region is fixed to Sydney. The account is whichever one the active AWS profile
belongs to (the ``cdk`` pixi task uses ``excelizarr-admin``), which the CLI passes in as
``CDK_DEFAULT_ACCOUNT``, so the account ID never has to live in this public repo.
Without credentials (e.g. ``pixi run synth``) it is unset and the template refers to
the account as ``AWS::AccountId`` instead.
"""

import os

import aws_cdk as cdk
from stacks import benchmark_stack

REGION = "ap-southeast-2"

app = cdk.App()
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION)

benchmark_stack(app, env)

app.synth()
