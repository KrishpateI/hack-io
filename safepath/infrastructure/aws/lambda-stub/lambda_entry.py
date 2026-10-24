"""Placeholder used by `cdk synth` when build/safepath-lambda.zip does not exist.

Never deploy this: run `python scripts/package_lambda.py` first (or set
`-c safepath:requireLambdaPackage=true` so synth fails without the package).
"""


def _stub(*_args, **_kwargs):
    return {"statusCode": 503, "body": "SafePath Lambda package not deployed (placeholder stub)."}


handler = sweeper_handler = websocket_handler = _stub
