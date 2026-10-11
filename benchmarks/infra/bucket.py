"""``CreateBucket``: the S3 bucket the benchmarks read xlsx and Zarr stores from.

The bucket is ``<name>-<account>``, unversioned, and removed along with its contents
when the stack is deleted, so put nothing in it that can't be regenerated.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct


class CreateBucket(Construct):
    """Create the benchmarking bucket, named ``<name>-<account>``."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        name: str,
    ) -> None:
        super().__init__(scope, construct_id)
        account = Stack.of(self).account

        self.bucket = s3.Bucket(
            self,
            "Bucket",
            bucket_name=f"{name}-{account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # auto_delete_objects adds a small Lambda that empties the bucket, so
            # ``cdk destroy`` works without emptying it by hand first.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
