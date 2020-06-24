"""
Build DAGs from dictionaries

The Python API provides great flexibility to build DAGs but some users
not need all of this. This module implements functions to parse dictionaries
and instantiate DAGs, only simple use cases should be handled by this API,
otherwise the dictionary schema will be too complex, defeating the purpose.

NOTE: CLI is implemented in the entry module
"""
import logging
from pathlib import Path
from collections.abc import MutableMapping, Mapping, Iterable

from ploomber import products
from ploomber import DAG, tasks
from ploomber.clients import SQLAlchemyClient
from ploomber.util.util import _load_factory
from ploomber.static_analysis import project

# TODO: make DAGSpec object which should validate schema and automatically
# fill with defaults all required but mussing sections, to avoid using
#  get_value_at

logger = logging.getLogger(__name__)


def normalize_task(task):
    if isinstance(task, str):
        return {'source': task}
    else:
        return task


class DAGSpec(MutableMapping):

    def __init__(self, data):
        if isinstance(data, list):
            data = {'tasks': data}

        data['tasks'] = [normalize_task(task) for task in data['tasks']]

        self.data = data
        self.validate_meta()

    def validate_meta(self):
        if 'meta' not in self.data:
            self.data['meta'] = {}

        if 'infer_upstream' not in self.data['meta']:
            self.data['meta']['infer_upstream'] = True

        if 'extract_product' not in self.data['meta']:
            self.data['meta']['extract_product'] = True

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __iter__(self):
        for key in self.data:
            yield key

    def __len__(self):
        return len(self.data)


def get_value_at(d, dotted_path):
    current = d

    for key in dotted_path.split('.'):
        try:
            current = current[key]
        except KeyError:
            return None

    return current


def _make_iterable(o):
    if isinstance(o, Iterable) and not isinstance(o, str):
        return o
    elif o is None:
        return []
    else:
        return [o]


def _pop_upstream(task_dict):
    upstream = task_dict.pop('upstream', None)
    return _make_iterable(upstream)


def _pop_product(task_dict, dag_spec):
    product_raw = task_dict.pop('product')

    product_class = get_value_at(dag_spec, 'meta.product_class')

    if 'product_class' in task_dict:
        CLASS = getattr(products, task_dict.pop('product_class'))
    elif product_class:
        CLASS = getattr(products, product_class)
    else:
        CLASS = products.File

    if isinstance(product_raw, Mapping):
        return {key: CLASS(value) for key, value in product_raw.items()}
    else:
        return CLASS(product_raw)


suffix2class = {
    '.py': tasks.NotebookRunner,
    '.ipynb': tasks.NotebookRunner,
    '.sql': tasks.SQLScript,
    '.sh': tasks.ShellScript
}


def get_task_class(task_dict):
    """
    Pops 'class' key if it exists

    Task class is determined by the 'class' key, if missing. Defaults
    are used by inspecting the 'source' key: NoteboonRunner (.py),
    SQLScript (.sql) and BashScript (.sh).
    """
    class_name = task_dict.pop('class', None)

    if class_name:
        class_ = getattr(tasks, class_name)
    else:
        suffix = Path(task_dict['source']).suffix

        if suffix2class.get(suffix):
            class_ = suffix2class[suffix]
        else:
            raise KeyError('No default task class available for task with '
                           'source: '
                           '"{}". Default class is only available for '
                           'files with extensions {}, otherwise you should '
                           'set an explicit class key'
                           .format(task_dict['source'], set(suffix2class)))

    return class_


def init_task(task_dict, dag, dag_spec):
    """Create a task from a dictionary

    """
    upstream = _pop_upstream(task_dict)
    class_ = get_task_class(task_dict)

    product = _pop_product(task_dict, dag_spec)
    source_raw = task_dict.pop('source')
    name_raw = task_dict.pop('name', None)

    task = class_(source=Path(source_raw),
                  product=product,
                  name=name_raw or source_raw,
                  dag=dag,
                  **task_dict)

    return task, upstream


def init_dag(dag_spec, root_path=None):
    """Create a dag from a spec
    """
    if 'location' in dag_spec:
        factory = _load_factory(dag_spec['location'])
        return factory()

    dag_spec = DAGSpec(dag_spec)

    tasks = dag_spec.pop('tasks')

    dag = DAG()

    config_clients = get_value_at(dag_spec, 'config.clients')

    if config_clients:
        init_clients(dag, config_clients)

    process_tasks(dag, tasks, dag_spec)

    return dag


def process_tasks(dag, tasks, dag_spec, root_path='.'):
    # determine if we need to run static analysis
    sources = [task_dict['source'] for task_dict in tasks]
    extracted = project.infer_from_path(root_path, templates=sources,
                                        upstream=dag_spec['meta']['infer_upstream'],
                                        product=dag_spec['meta']['extract_product'])

    upstream = {}

    for task_dict in tasks:
        source = task_dict['source']

        if dag_spec['meta']['infer_upstream']:
            task_dict['upstream'] = extracted['upstream'][source]

        if dag_spec['meta']['extract_product']:
            task_dict['product'] = extracted['product'][source]

        task, up = init_task(task_dict, dag, dag_spec)
        upstream[task] = up

    # once we added all tasks, set upstream dependencies
    for task in list(dag.values()):
        for task_dep in upstream[task]:
            task.set_upstream(dag[task_dep])


def init_clients(dag, clients):
    for class_name, dotted_path in clients.items():

        class_ = getattr(tasks, class_name, None)

        if not class_:
            class_ = getattr(products, class_name)

        dag.clients[class_] = SQLAlchemyClient(_load_factory(dotted_path)())
