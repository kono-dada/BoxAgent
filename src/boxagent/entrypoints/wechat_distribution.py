"""Build tool; end users never need to find or download a WeChat installer."""
import argparse
import json
from pathlib import Path

from boxagent.bootstrap.wechat_distribution import prepare_wechat_distribution


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare_wechat_distribution(arguments.source, arguments.output)))


if __name__ == '__main__':
    main()
