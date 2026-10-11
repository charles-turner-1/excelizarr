# Cloud infra for benchmarking

A CDK app that deploys one S3 bucket, `excelizarr-bench-<account>`, so the benchmarks
can compare excelizarr with plain old HDF5 in a real object store.

It deploys with the `excelizarr-admin` AWS profile (an SSO profile in `~/.aws/config`).
The `cdk` task logs in first if the session has expired.

```sh
pixi run -e bench synth          # build the template; no AWS access needed
pixi run -e bench cdk diff       # what a deploy would change
pixi run -e bench cdk deploy
pixi run -e bench cdk destroy    # deletes the bucket *and everything in it*
```

The account must be bootstrapped once (`pixi run -e bench cdk bootstrap`); this one
already is.
