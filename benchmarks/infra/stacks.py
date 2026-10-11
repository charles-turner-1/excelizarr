"""The ``excelizarr-bench-benchmark`` stack: one S3 bucket to benchmark reads against.

Don't rename ``DEPLOYMENT`` or the construct IDs while the stack is deployed: CDK
derives the stack and resource names from them, so a rename creates a new stack (whose
bucket name clashes with the old one) or replaces the bucket.
"""

from __future__ import annotations

import aws_cdk as cdk
from bucket import CreateBucket
from constructs import Construct

DEPLOYMENT = "excelizarr-bench"


def benchmark_stack(scope: Construct, env: cdk.Environment) -> cdk.Stack:
    """Create the benchmarking stack and its bucket."""
    stack = cdk.Stack(scope, f"{DEPLOYMENT}-benchmark", env=env)

    bench = CreateBucket(stack, "Benchmarking", name=DEPLOYMENT)

    cdk.CfnOutput(stack, "BucketName", value=bench.bucket.bucket_name)
    return stack
