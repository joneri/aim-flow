"""Journaled inert staging through the local Docker CLI and exact Engine observations."""
from base64 import b64encode
from hashlib import sha256, sha512
import os
from pathlib import Path
import re
import subprocess
import tempfile

from openai_budget import Refused, fingerprint
from . import docker_engine
from .effects import EffectJournal
from .owned_paths import owned_file, owned_mkdir
from .recipe_plan import container_runtime_sha

_ZERO = '0001-01-01T00:00:00Z'
_LABEL = 'org.aim-ci.preparation'
_MAX_ARCHIVE = 550 * 1024 * 1024


def measure_file(stream, limit, *, with_sri=False):
    sha, sri = sha256(), sha512()
    count = 0
    while chunk := stream.read(min(64 * 1024, limit - count + 1)):
        count += len(chunk)
        if count > limit:
            raise Refused('Preparation input exceeds its byte limit')
        sha.update(chunk)
        if with_sri:
            sri.update(chunk)
    result = {'sha256': sha.hexdigest(), 'size': count}
    if with_sri:
        result['integrity'] = 'sha512-' + b64encode(sri.digest()).decode()
    return result


class ImagePreparation:
    def __init__(self, output, intent, intent_pin, before_effect):
        self.output, self.intent, self.intent_pin = output, intent, intent_pin
        self.base = intent['base']['imageDigest']
        self.name = 'aim-ci-prepare-' + intent_pin[:24]
        self.tag = 'aim-ci-prepared:' + intent_pin[:24]
        self.endpoint = intent['docker']['endpoint']
        self.engine = docker_engine.DockerEngine(self.endpoint, docker_engine.unix_request)
        self.argv = [intent['docker']['path'], '--host', self.endpoint]
        self.before_effect = before_effect
        target = intent['target']
        self.labels = docker_engine.recipe_image_labels(
            {'sourceCommit': target['commit'], 'sourceTree': target['tree'],
             'runtimeSha256': container_runtime_sha(intent['base']['tools'], intent['base'].get('browserRuntime'))},
            intent['storeIndexSha256'])
        self.labels[_LABEL] = intent_pin
        owned_mkdir(output, 'image-journal')
        self.journal = EffectJournal(output/'image-journal', {'scope': 'one-inert-image-preparation',
                                                             'intentSha256': intent_pin})

    def cli(self, *args, stdin=None, timeout=20):
        try:
            with tempfile.TemporaryDirectory(dir=self.output, prefix='docker-client-') as home:
                result = subprocess.run([*self.argv, *args], cwd=self.output, stdin=stdin, capture_output=True,
                    timeout=timeout, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': home,
                                          'DOCKER_CONFIG': home})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Refused(f'Docker staging {args[1]} outcome is unknown ({type(error).__name__})') from None
        if len(result.stdout) > 1_000_000 or len(result.stderr) > 1_000_000:
            raise Refused(f'Docker staging {args[1]} response exceeds its bound')
        if result.returncode:
            detail = result.stderr[:512].decode(errors='replace').strip()
            raise Refused(f'Docker staging {args[1]} exited {result.returncode}: {detail}')
        return result.stdout

    def effect(self, name, intent, action, observe=None):
        def receipt(key, value):
            return self.journal.receipt(key, fingerprint(intent), value)

        def lookup(key, _):
            value = observe()
            return {'state': 'unknown'} if value is None else {'state': 'completed', 'receipt': receipt(key, value)}

        return self.journal.perform('prepare:'+name, intent, lambda key, _: receipt(key, action()),
                                    lookup=lookup if observe else None)

    def inspect_image(self, selector):
        return self.engine._call('GET', f'/images/{selector}/json', (200, 404))

    def checked_base(self):
        image = self.inspect_image(self.base)
        config = image.get('Config') if image else None
        filesystem = image.get('RootFS') if image else None
        layers = filesystem.get('Layers') if isinstance(filesystem, dict) else None
        env = config.get('Env') if isinstance(config, dict) else None
        labels = config.get('Labels') if isinstance(config, dict) else None
        if labels is None:
            labels = {}
        platform = self.intent['platform']
        if (not image or image.get('Id') != self.base or image.get('Os') != platform['os']
                or image.get('Architecture') != {'x64': 'amd64', 'arm64': 'arm64'}[platform['cpu']]
                or not isinstance(config, dict) or config.get('Volumes')
                or not isinstance(labels, dict) or not all(isinstance(value, str) for value in labels.values())
                or not isinstance(layers, list) or not layers
                or any(not isinstance(layer, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', layer) for layer in layers)
                or not isinstance(env, list) or len(env) > 3
                or any(not isinstance(item, str) or '=' not in item
                       or item.split('=', 1)[0] not in {'PATH', 'NODE_VERSION', 'YARN_VERSION'} for item in env)
                or len({item.split('=', 1)[0] for item in env}) != len(env)):
            raise Refused('Preloaded Docker base does not match the reviewed platform and safe image layout')
        # Docker preserves base labels and applies the controller's overrides on commit.
        return {'layers': layers, 'env': env,
                'derivedLabelsSha256': fingerprint({**labels, **self.labels})}

    def stage(self, selector):
        row = self.engine._call('GET', f'/containers/{selector}/json', (200, 404))
        if row is None:
            return None
        identifier = docker_engine._container_id(row.get('Id'))
        config, host, state = (row.get(key) for key in ('Config', 'HostConfig', 'State'))
        if (selector not in (identifier, self.name) or row.get('Name') != '/'+self.name
                or row.get('Image') != self.base or row.get('Mounts') != []
                or not all(isinstance(value, dict) for value in (config, host, state))
                or config.get('Entrypoint') != ['/bin/false'] or config.get('Cmd') != ['--aim-ci-preparation-only']
                or not isinstance(config.get('Labels'), dict) or config['Labels'].get(_LABEL) != self.intent_pin
                or config.get('Volumes') or host.get('NetworkMode') != 'none' or host.get('Privileged') is not False
                or host.get('ReadonlyRootfs') is not False or host.get('AutoRemove') is not False
                or host.get('CapDrop') != ['ALL'] or host.get('SecurityOpt') not in (
                    ['no-new-privileges'], ['no-new-privileges=true'], ['no-new-privileges:true'])
                or host.get('RestartPolicy', {}).get('Name') != 'no'
                or any(host.get(key) for key in ('Binds', 'Mounts', 'Tmpfs', 'Devices', 'DeviceRequests',
                                                'VolumesFrom', 'PortBindings', 'PublishAllPorts'))
                or state.get('Status') != 'created' or type(state.get('Pid')) is not int or state['Pid'] != 0
                or any(state.get(key) is not False for key in ('Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled'))
                or state.get('StartedAt') != _ZERO or state.get('FinishedAt') != _ZERO):
            raise Refused('Staging container is not the exact owned never-started container')
        return {'containerId': identifier, 'neverStarted': True}

    def derived(self, selector, base, *, final=False):
        image = self.inspect_image(selector)
        if image is None:
            return None
        identifier = image.get('Id')
        config = image.get('Config')
        labels = config.get('Labels') if isinstance(config, dict) else None
        filesystem = image.get('RootFS')
        layers = filesystem.get('Layers') if isinstance(filesystem, dict) else None
        platform = self.intent['platform']
        if (not isinstance(identifier, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', identifier)
                or identifier == self.base or selector not in (identifier, self.tag)
                or image.get('RepoTags') != [self.tag] or image.get('Os') != platform['os']
                or image.get('Architecture') != {'x64': 'amd64', 'arm64': 'arm64'}[platform['cpu']]
                or not isinstance(labels, dict) or not all(isinstance(value, str) for value in labels.values())
                or fingerprint(labels) != base['derivedLabelsSha256']
                or config.get('Volumes') or config.get('Env') != base['env']
                or not isinstance(layers, list) or not layers or layers[:-1] != base['layers']
                or not isinstance(layers[-1], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', layers[-1])):
            raise Refused('Derived image does not match the exact reviewed staging effect')
        if final and image.get('RepoDigests') not in (None, [], ['aim-ci-prepared@' + identifier]):
            raise Refused('Derived image has unexpected digest references')
        return {'imageId': identifier, 'tag': self.tag, 'baseImage': self.base, 'labels': self.labels,
                'daemonByteReadback': 'pending-separately-approved-build'}

    def created(self):
        args = ('container', 'create', '--name', self.name, '--pull', 'never', '--network', 'none',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--restart', 'no', '--no-healthcheck',
                '--label', f'{_LABEL}={self.intent_pin}', '--entrypoint', '/bin/false',
                self.base, '--aim-ci-preparation-only')
        return args, {'argv': [*self.argv, *args], 'baseImage': self.base, 'name': self.name}

    def cleanup(self):
        if self.journal.inspect('prepare:create') is None:
            return {'status': 'no-staging-effect-recorded'}
        _, intent = self.created()

        def forbidden():
            raise Refused('Preparation cleanup cannot create a container')

        identifier = self.effect('create', intent, forbidden, lambda: self.stage(self.name))['containerId']

        def absent():
            return {'status': 'confirmed-absent', 'containerId': identifier} if self.stage(identifier) is None else None

        def remove():
            if absent() is None:
                self.engine.remove(identifier)
            result = absent()
            if result is None:
                raise Refused('Exact staging cleanup is not confirmed')
            return result

        return self.effect('remove', {'containerId': identifier, 'name': self.name}, remove, absent)

    def commit_args(self, identifier):
        labels = [part for key, value in sorted(self.labels.items()) for part in ('--change', f'LABEL {key}={value}')]
        return ('container', 'commit', '--no-pause', *labels, identifier, self.tag)

    def final_image(self, image_id, prepared_image, archive, *, preview=False):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id) or image_id == self.base:
            raise Refused('Final cleanup requires a full derived image ID distinct from the base')
        _, create = self.created()
        created = self.journal.completed_result('prepare:create', create)
        if not isinstance(created, dict):
            raise Refused('Malformed completed staging receipt')
        identifier = docker_engine._container_id(created.get('containerId'))
        uploaded = self.journal.completed_result('prepare:upload', {
            'argv': [*self.argv, 'container', 'cp', '-', identifier + ':/'], 'archive': archive})
        removed = self.journal.completed_result('prepare:remove', {'containerId': identifier, 'name': self.name})
        if (created != {'containerId': identifier, 'neverStarted': True}
                or uploaded != {'containerId': identifier, 'archive': archive}
                or removed != {'containerId': identifier, 'status': 'confirmed-absent'}):
            raise Refused('Final cleanup requires completed inert staging and container removal')
        commit = self.journal.inspect('prepare:commit')
        bound = commit.get('intent') if commit else None
        base = bound.get('base') if isinstance(bound, dict) else None
        if (not isinstance(base, dict) or set(base) != {'layers', 'env', 'derivedLabelsSha256'}
                or not isinstance(base['layers'], list) or not base['layers']
                or not all(isinstance(layer, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', layer)
                           for layer in base['layers'])
                or not isinstance(base['env'], list) or not all(isinstance(item, str) for item in base['env'])
                or not isinstance(base['derivedLabelsSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', base['derivedLabelsSha256'])):
            raise Refused('Missing recorded base layers, environment or derived label identity')
        expected = {'imageId': image_id, 'tag': self.tag, 'baseImage': self.base, 'labels': self.labels,
                    'daemonByteReadback': 'pending-separately-approved-build'}
        committed = self.journal.completed_result('prepare:commit',
            {'argv': [*self.argv, *self.commit_args(identifier)], 'base': base})
        if committed != expected or prepared_image != expected:
            raise Refused('Derived image ID differs from the completed preparation')

        args = ('image', 'rm', '--no-prune', image_id)
        request = {'argv': [*self.argv, *args], 'imageId': image_id, 'tag': self.tag, 'baseImage': self.base}
        operation = self.journal.inspect('prepare:remove-image')
        if operation is not None and (operation.get('intent') != request
                                      or operation.get('state') not in ('pending', 'unknown', 'completed')):
            raise Refused('Retained image removal does not match this exact effect')
        if preview:
            return {'status': 'image-cleanup-preview', 'intentSha256': self.intent_pin,
                    'imageRemoval': request, 'journalState': operation['state'] if operation else 'not-dispatched',
                    'dockerContacted': False, 'executionAuthorized': False, 'derivedImageRemoved': False}

        def observe():
            if self.checked_base() != base:
                raise Refused('Cached base metadata differs from the completed preparation')
            by_id = self.derived(image_id, base, final=True)
            by_tag = self.derived(self.tag, base, final=True)
            if by_id is None and by_tag is None:
                return False
            if by_id != expected or by_tag != expected:
                raise Refused('Derived image ID and sole tag no longer resolve to the same owned image')
            return True

        result = {'status': 'confirmed-absent', 'imageId': image_id, 'tag': self.tag, 'baseImage': self.base}

        def absent():
            return None if observe() else result

        def ready():
            if not observe():
                raise Refused('Derived image is absent without a prior removal intent')
            if self.cli('container', 'ls', '--all', '--quiet', '--no-trunc', '--filter', 'ancestor=' + image_id).strip():
                raise Refused('Derived image is referenced by a container')
            if not observe():
                raise Refused('Derived image changed before removal')

        def remove():
            self.before_effect()
            ready()
            self.cli(*args)
            if absent() is None:
                raise Refused('Derived image removal is not confirmed')
            return result

        if operation is None:
            ready()
        retained = self.effect('remove-image', request, remove, absent)
        if retained != result or absent() is None:
            raise Refused('Retained derived image removal is not confirmed')
        return retained

    def run(self, archive_name, archive_pin):
        base = self.checked_base()
        args, create_intent = self.created()

        def create():
            if self.inspect_image(self.tag) is not None or self.stage(self.name) is not None:
                raise Refused('Preparation image tag or container name is already present')
            self.before_effect()
            identifier = docker_engine._container_id(self.cli(*args).decode().strip())
            observed = self.stage(identifier)
            if observed is None:
                raise Refused('Docker staging creation is not confirmed')
            return observed

        try:
            stage = self.effect('create', create_intent, create, lambda: self.stage(self.name))
            identifier = stage['containerId']
            copy = ('container', 'cp', '-', identifier + ':/')

            def upload():
                if self.stage(identifier) != stage:
                    raise Refused('Staging container changed before source upload')
                with os.fdopen(owned_file(self.output, archive_name, os.O_RDONLY), 'rb') as stream:
                    if measure_file(stream, _MAX_ARCHIVE) != archive_pin:
                        raise Refused('Prepared archive changed before upload')
                    stream.seek(0)
                    self.before_effect()
                    self.cli(*copy, stdin=stream, timeout=120)
                if self.stage(identifier) != stage:
                    raise Refused('Staging container changed after source upload')
                changes = self.cli('container', 'diff', identifier).decode().splitlines()
                if (not any(row == 'A /opt/aim-ci/npm_platform_build.cjs' for row in changes)
                        or any(not re.fullmatch(r'[AC] /opt(?:/aim-ci(?:/.*)?)?', row) for row in changes)):
                    raise Refused('Staging layer changed paths outside the reviewed source layout')
                return {'containerId': identifier, 'archive': archive_pin}

            self.effect('upload', {'argv': [*self.argv, *copy], 'archive': archive_pin}, upload)
            commit = self.commit_args(identifier)

            def publish():
                if self.stage(identifier) != stage:
                    raise Refused('Staging container changed before committing the image')
                self.before_effect()
                image_id = self.cli(*commit, timeout=120).decode().strip()
                if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
                    raise Refused('Docker did not return a full derived image ID')
                result = self.derived(image_id, base)
                if result is None:
                    raise Refused('Derived Docker image is not confirmed')
                return result

            result = self.effect('commit', {'argv': [*self.argv, *commit], 'base': base}, publish,
                                 lambda: self.derived(self.tag, base))
        finally:
            self.cleanup()
        if self.derived(result['imageId'], base) != result:
            raise Refused('Retained prepared image is unavailable or changed')
        return result
