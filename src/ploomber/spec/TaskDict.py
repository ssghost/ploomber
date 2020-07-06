"""
Create Tasks from dictionaries
"""
from pathlib import Path
from collections.abc import MutableMapping, Iterable, Mapping

from ploomber import tasks, products
from ploomber.util.util import _load_factory
from ploomber.spec import validate

suffix2taskclass = {
    '.py': tasks.NotebookRunner,
    '.ipynb': tasks.NotebookRunner,
    '.sql': tasks.SQLScript,
    '.sh': tasks.ShellScript
}


class TaskDict(MutableMapping):
    def __init__(self, data, meta):
        # FIXME: make sure data and meta are immutable structures
        self.data = data
        self.meta = meta
        self.validate()

    def validate(self):
        if self.meta['extract_product']:
            required = {'source'}
        else:
            required = {'product', 'source'}

        validate.keys(valid=None, passed=self.data,
                      required=required,
                      name='task spec')

        if self.meta['extract_upstream'] and self.data.get('upstream'):
            raise ValueError('Error validating task "{}", if '
                             'meta.extract_upstream is set to True, tasks '
                             'should not have an "upstream" key'
                             .format(self.data))

        if self.meta['extract_product'] and self.data.get('product'):
            raise ValueError('Error validating task "{}", if '
                             'meta.extract_product is set to True, tasks '
                             'should not have a "product" key'
                             .format(self.data))

    def to_task(self, dag):
        """Returns a Task instance
        """
        task_dict = self.data
        upstream = _pop_upstream(task_dict)
        class_ = get_task_class(task_dict)

        product = _init_product(task_dict, self.meta, class_)

        _init_client(task_dict)

        source_raw = task_dict.pop('source')
        name_raw = task_dict.pop('name', None)

        task = class_(source=Path(source_raw),
                      product=product,
                      name=name_raw or source_raw,
                      dag=dag,
                      **task_dict)

        return task, upstream

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __iter__(self):
        for e in self.data:
            yield e

    def __len__(self):
        return len(self.data)


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


# FIXME: how do we make a default product client? use the task's client?
def _init_product(task_dict, meta, task_class):
    """
    Resolution logic order:
        task.product_class
        meta.{task_class}.product_default_class

    Current limitation: When there is more than one product, they all must
    be from the same class.
    """
    product_raw = task_dict.pop('product')

    key = 'product_default_class.'+task_class.__name__
    meta_product_default_class = get_value_at(meta, key)

    if 'product_class' in task_dict:
        CLASS = getattr(products, task_dict.pop('product_class'))
    elif meta_product_default_class:
        CLASS = getattr(products, meta_product_default_class)
    else:
        raise ValueError('Could not determine a product class for task: '
                         '"{}". Add an explicity value in the '
                         '"product_class" key or provide a default value in '
                         'meta.product_default_class by setting the '
                         'key to the applicable task class'
                         .format(task_dict))

    if 'product_client' in task_dict:
        dotted_path = task_dict.pop('product_client')
        kwargs = {'client': _load_factory(dotted_path)()}
    else:
        kwargs = {}

    relative_to = (Path(task_dict['source']).parent
                   if meta['product_relative_to_source'] else False)

    if isinstance(product_raw, Mapping):
        return {key: CLASS(resolve_product(value,
                                           relative_to, CLASS), **kwargs)
                for key, value in product_raw.items()}
    else:
        return CLASS(resolve_product(product_raw,
                                     relative_to, CLASS), **kwargs)


def resolve_product(product_raw, relative_to, class_):
    if not relative_to or class_ != products.File:
        return product_raw
    else:
        # we call resolve in relative_to and then append the rest because
        # Python 3.5 raises a FileNotFoundError is calling resolve in a path
        # that does not exist
        relative_to = Path(relative_to).resolve()
        return str(Path(relative_to, product_raw))


def _init_client(task_dict):
    if 'client' in task_dict:
        dotted_path = task_dict.pop('client')
        task_dict['client'] = _load_factory(dotted_path)()


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

        if suffix2taskclass.get(suffix):
            class_ = suffix2taskclass[suffix]
        else:
            raise KeyError('No default task class available for task with '
                           'source: '
                           '"{}". Default class is only available for '
                           'files with extensions {}, otherwise you should '
                           'set an explicit class key'
                           .format(task_dict['source'], set(suffix2taskclass)))

    return class_


def get_value_at(d, dotted_path):
    current = d

    for key in dotted_path.split('.'):
        try:
            current = current[key]
        except KeyError:
            return None

    return current