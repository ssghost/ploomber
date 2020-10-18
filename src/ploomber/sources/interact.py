from itertools import chain
from pathlib import Path
import inspect

import jupyter_client
import papermill
import parso
import nbformat


class CallableInteractiveDeveloper:
    """Convert callables to notebooks, edit and save back

    Parameters
    ----------
    fn : callable
        Function to edit
    params : dict
        Parameters to call the function

    Examples
    --------
    >>> wih CallableInteractiveDeveloper(fn, {'param': 1}) as path_to_nb:
    ...     # do stuff with the notebook file
    ...     pass
    """
    def __init__(self, fn, params):
        self.fn = fn
        self.path_to_source = Path(inspect.getsourcefile(fn))
        lines, start = inspect.getsourcelines(fn)
        self.lines_num_limits = (start, start + len(lines))
        self.params = params
        self.tmp_path = self.path_to_source.with_name(
            self.path_to_source.with_suffix('').name + '-tmp.ipynb')
        self.body_start = None
        self._source_code = None

    def _to_nb(self):
        """
        Returns the function's body in a notebook (tmp location), inserts
        params as variables at the top
        """
        body_elements, self.body_start, imports_cell = parse_function(self.fn)
        function_to_nb(body_elements, self.tmp_path, imports_cell)

        # TODO: inject cells with imports + functions + classes defined
        # in the file (should make this the cwd for imports in non-packages
        # to work though) - if i do that, then I should probably do the same
        # for notebook runner to be consistent

        papermill.execute_notebook(str(self.tmp_path),
                                   str(self.tmp_path),
                                   prepare_only=True,
                                   parameters=self.params)

        return self.tmp_path

    def _overwrite_from_nb(self, path):
        """
        Overwrite the function's body with the notebook contents, excluding
        injected parameters and cells whose first line is "#"
        """
        nb = nbformat.read(path, as_version=nbformat.NO_CONVERT)

        # remove cells that are only needed for the nb but not for the function
        code_cells = [c['source'] for c in nb.cells if keep_cell(c)]

        # add 4 spaces to each code cell, exclude white space lines
        code_cells = [indent_cell(code) for code in code_cells]

        # get the original file where the function is defined
        content = self.path_to_source.read_text()
        content_lines = content.splitlines()
        trailing_newline = content[-1] == '\n'
        fn_starts, fn_ends = self.lines_num_limits

        # keep the file the same until you reach the function definition plus
        # an offset to account for the signature (which might span >1 line)
        keep_until = fn_starts + self.body_start
        header = content_lines[:keep_until]

        # the footer is everything below the end of the original definition
        footer = content_lines[fn_ends:]

        # if there is anything at the end, we have to add an empty line to
        # properly end the function definition, if this is the last definition
        # in the file, we don't have to add this
        if footer:
            footer = [''] + footer

        new_content = '\n'.join(header + code_cells + footer)

        # if the original hile had a trailing newline, keep it
        if trailing_newline:
            new_content += '\n'

        # finally add new imports, if any
        imports_new = get_imports_new_source(nb)

        if imports_new:
            new_content = imports_new + new_content

        self.path_to_source.write_text(new_content)

    def __enter__(self):
        self._source_code = self.path_to_source.read_text()
        self.tmp_path = self._to_nb()
        return str(self.tmp_path)

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_source_code = self.path_to_source.read_text()

        if self._source_code != current_source_code:
            raise ValueError(f'File "{self.path_to_source}" (where '
                             f'callable "{self.fn.__name__}" is defined) '
                             'changed while editing the function in the '
                             'notebook app. This might lead to corrupted '
                             'source files. Changes from the notebook were '
                             'not saved back to the module. Notebook '
                             f'available at "{self.tmp_path}')

        self._overwrite_from_nb(self.tmp_path)
        Path(self.tmp_path).unlink()

    def __del__(self):
        tmp = Path(self.tmp_path)
        if tmp.exists():
            tmp.unlink()


def keep_cell(cell):
    """
    Rule to decide whether to keep a cell or not. This is executed before
    converting the notebook back to a function
    """
    tags = set(cell['metadata'].get('tags', {}))
    tmp_tags = {'injected-parameters', 'imports', 'imports-new'}
    has_tmp_tags = len(tags & tmp_tags)

    return (cell['cell_type'] == 'code' and not has_tmp_tags
            and cell['source'][:2] != '#\n')


def indent_line(lline):
    return '    ' + lline if lline else ''


def indent_cell(code):
    return '\n'.join([indent_line(line) for line in code.splitlines()])


def parse_function(fn):
    """
    Extract function's source code, parse it and return function body
    elements along with the # of the last line for the signature (which
    marks the beginning of the function's body) and all the imports
    """
    # TODO: exclude return at the end, what if we find more than one?
    # maybe do not support functions with return statements for now

    # getsource adds a new line at the end of the the function, we don't need
    # this
    s = inspect.getsource(fn).rstrip()
    body = parso.parse(s).children[0].children[-1]

    # parso is adding a new line as first element, not sure if this
    # happens always though
    if isinstance(body.children[0], parso.python.tree.Newline):
        body_elements = body.children[1:]
    else:
        body_elements = body.children

    # get imports in the corresponding module
    module = parso.parse(Path(inspect.getfile(fn)).read_text())
    imports_statements = '\n'.join(
        [imp.get_code() for imp in module.iter_imports()])
    # add local definitions
    imports_local = make_import_from_definitions(module, fn)
    imports_cell = imports_statements + '\n' + imports_local

    return body_elements, body.start_pos[0] - 1, imports_cell


def get_func_and_class_names(module):
    return [
        defs.name.get_code().strip()
        for defs in chain(module.iter_funcdefs(), module.iter_classdefs())
    ]


def get_imports_new_source(nb):
    """
    Returns the source code of the first cell tagged 'imports-new', strips
    out comments
    """
    source = None

    for cell in nb.cells:
        if 'imports-new' in cell['metadata'].get('tags', {}):
            source = cell.source
            break

    if source:
        lines = [
            line for line in source.splitlines() if not line.startswith('#')
        ]

        if lines:
            return '\n'.join(lines) + '\n'


def make_import_from_definitions(module, fn):
    module_name = inspect.getmodule(fn).__name__
    names = [
        name for name in get_func_and_class_names(module)
        if name != fn.__name__
    ]
    names_all = ', '.join(names)
    return f'from {module_name} import {names_all}'


def function_to_nb(body_elements, path, imports_cell):
    """
    Save function body elements to a notebook
    """
    nb_format = nbformat.versions[nbformat.current_nbformat]
    nb = nb_format.new_notebook()

    # first cell: add imports cell
    nb.cells.append(
        nb_format.new_code_cell(source=imports_cell,
                                metadata=dict(tags=['imports'])))

    # second cell: added imports, in case the user wants to add any imports
    # back to the original module
    imports_new_comment = (
        '# Use this cell to include any imports that you '
        'want to save back\n# to the top of the module, comments will be '
        'ignored')
    nb.cells.append(
        nb_format.new_code_cell(source=imports_new_comment,
                                metadata=dict(tags=['imports-new'])))

    for statement in body_elements:
        # parso incluses new line tokens, remove any trailing whitespace
        lines = [
            line[4:] for line in statement.get_code().rstrip().split('\n')
        ]
        cell = nb_format.new_code_cell(source='\n'.join(lines))
        nb.cells.append(cell)

    k = jupyter_client.kernelspec.get_kernel_spec('python3')

    nb.metadata.kernelspec = {
        "display_name": k.display_name,
        "language": k.language,
        "name": 'python3'
    }

    nbformat.write(nb, path)