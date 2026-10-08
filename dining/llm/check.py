"""Inspect model configuration, or --send one fictional explanation-contract probe."""

import argparse
import json
from pathlib import Path

from dining.core.runtime_env import load_env_file
from dining.llm.inference import InferenceSettings, invoke_explanations


def main(argv=None):
    """Check the language-model configuration; ``--send`` makes one synthetic request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send one synthetic provider request; may incur usage charges",
    )
    args = parser.parse_args(argv)
    try:
        load_env_file(args.env_file)
    except ValueError:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_code": "env_file_unreadable",
                    "model_calls": 0,
                    "provider_contract_verified": False,
                }
            )
        )
        return 2
    settings = InferenceSettings.from_env()
    report = {
        **settings.public(),
        "status": "not_run",
        "model_calls": 0,
        "provider_contract_verified": False,
        "scope": "Synthetic explanation-label contract only; not ranking quality or a complete dining journey.",
    }
    if args.send:
        inference = invoke_explanations(settings, ["probe-option-a", "probe-option-b"])
        for key in (
            "model_calls",
            "inference_status",
            "model_error_code",
            "input_tokens",
            "output_tokens",
            "usage_source",
            "returned_model_id",
        ):
            report[key] = inference.metadata[key]
        report["status"] = inference.metadata["inference_status"]
        report["provider_contract_verified"] = inference.labels is not None
    print(json.dumps(report, indent=2))
    if args.send:
        return 0 if report["provider_contract_verified"] else 2
    return 2 if settings.error_code else 0


if __name__ == "__main__":
    raise SystemExit(main())
