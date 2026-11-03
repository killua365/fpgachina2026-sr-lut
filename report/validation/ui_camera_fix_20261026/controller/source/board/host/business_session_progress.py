"""Read controller and worker together; controller closure takes precedence."""
import argparse
import json
from pathlib import Path


def progress(run):
    live = json.loads((run/'live_status.json').read_text('utf-8'))
    files = [run/'source_latest.json'] if (run/'source_latest.json').exists() else sorted(run.glob('source_[0-9]*.json'))
    source = json.loads(files[-1].read_text('utf-8')) if files else {}
    worker, monitor = source.get('worker', {}), live['monitor']
    closed = monitor.get('sample_close') is not None
    allowed = (monitor['state'] == 'sampling' and not closed and not monitor['failures']
               and source.get('state') == 'running' and worker.get('phase') == 'interactive_business')
    return dict(controller_state=monitor['state'], controller_failures=monitor['failures'],
        sampling_closed=closed,
        operator_stop_needed=(monitor['state']=='stopping_source' and source.get('state') not in ('finalizing','finished')),
        further_interactions_allowed=allowed, source_state=source.get('state'), phase=worker.get('phase'),
        end_mode=worker.get('end_mode', 'timed'),
        remaining_seconds=worker.get('workspace',{}).get('session_remaining_seconds'),
        runtime=worker.get('runtime',{}), worker_errors=worker.get('errors',[]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    value = progress(parser.parse_args().run)
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(2 if value['controller_failures'] else 0)
