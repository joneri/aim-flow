#!/usr/bin/env python3
"""AIM Flow Beta: the compatibility-preserving local controller entry point."""
import runpy
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit('AIM Flow Beta requires Python 3.11 or newer.')

if sys.argv[1:] == ['--version']:
    print('AIM Flow 0.1.0-beta.8')
elif sys.argv[1:2] == ['onboard-api']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_onboarding_delivery import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['deliver-api']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_api_delivery import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
else:
    runpy.run_path(str(Path(__file__).resolve().with_name('aim-ci-control.py')), run_name='__main__')
