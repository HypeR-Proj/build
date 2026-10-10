#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Name an application feature variant independently of argument ordering."""

import argparse
import hashlib
import json


def variant(features, binaries):
    configuration = [sorted(set(features.replace(',', ' ').split())),
                     sorted(set(binaries.split()))]
    return 'features-' + hashlib.sha256(json.dumps(configuration).encode()).hexdigest()[:16]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features', default='')
    parser.add_argument('--bins', default='')
    args = parser.parse_args()
    print(variant(args.features, args.bins))
