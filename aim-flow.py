#!/usr/bin/env python3
"""AIM Flow Beta: the compatibility-preserving local controller entry point."""
import json
import re
import runpy
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit('AIM Flow Beta requires Python 3.11 or newer.')

if sys.argv[1:] == ['--version']:
    # This displays a local label only. Readiness still verifies the exact pins.
    root = Path(__file__).resolve().parent
    manifest = root / 'control-manifest.json'
    try:
        version_file = manifest if manifest.exists() else root / 'VERSION'
        with version_file.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('Oversized version metadata')
        value = json.loads(raw).get('version') if manifest.exists() else raw.decode('utf-8').strip()
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*)?', value):
            raise ValueError('Invalid release version')
    except (OSError, ValueError, AttributeError):
        raise SystemExit('Cannot read a valid local AIM Flow version.')
    print('AIM Flow ' + value)
elif sys.argv[1:2] == ['reconcile-runtime']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_runtime_recovery import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['accept-local']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_owner_acceptance import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['deliver']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_delivery_chain import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['continue-release']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_release_continuation import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['publish-github']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_github_publication import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['deliver-github']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_github_delivery import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['review-candidate-api']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_candidate_review import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['derive-intake']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_requirement_intake import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['repair-and-validate']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_repair_run import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['repair-candidate']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_repair_candidate import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['resume-candidate']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_resume_candidate import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['run']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_run import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['review-executed-work']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_execution_review import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['validate-work']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_workflow_validation import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
elif sys.argv[1:2] == ['work']:
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
    from flow_workflow import main
    try:
        raise SystemExit(main(sys.argv[2:]))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
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
