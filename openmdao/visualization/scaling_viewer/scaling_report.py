
"""Define a function to view driver scaling."""
import os
import sys
import json
from itertools import chain
from collections import defaultdict

import numpy as np

import openmdao
import openmdao.utils.coloring as coloring_mod
import openmdao.utils.hooks as hooks
from openmdao.core.problem import Problem
from openmdao.utils.units import convert_units
from openmdao.utils.mpi import MPI
from openmdao.utils.webview import webview
from openmdao.utils.general_utils import printoptions, ignore_errors, default_noraise
from openmdao.utils.file_utils import _load_and_exec, _to_filename


def _val2str(val):
    if isinstance(val, np.ndarray):
        if val.size > 5:
            return 'array %s' % str(val.shape)
        else:
            return np.array2string(val)

    return str(val)


def _unscale(val, scaler, adder, default=''):
    if val is None:
        return default
    if scaler is not None:
        val = val * (1.0 / scaler)
    if adder is not None:
        val = val - adder
    return val


def _scale(val, scaler, adder, unset=''):
    if val is None:
        return unset
    if adder is not None:
        val = val + adder
    if scaler is not None:
        val = val * scaler
    return val


def _getdef(val, unset):
    if val is None:
        return unset
    if np.isscalar(val) and (val == openmdao.INF_BOUND or val == -openmdao.INF_BOUND):
        return unset
    return val


def _getnorm(val, unset=''):
    val = _getdef(val, unset)
    if np.isscalar(val) or val.size == 1:
        return val
    return np.linalg.norm(val)


def _getnorm_and_size(val, unset=''):
    # return norm and the size of the value
    val = _getdef(val, unset)
    if np.isscalar(val) or val.size == 1:
        return [val, 1]
    return [np.linalg.norm(val), val.size]


def _get_flat(val, size):
    if val is None:
        return val
    elif np.isscalar(val):
        return np.full(size, val)
    elif val.size > 1:
        return val.flatten()
    return np.full(size, val[0])


def _add_child_rows(row, mval, dval, scaler=None, adder=None, ref=None, ref0=None,
                    lower=None, upper=None, equals=None):
    if not (np.isscalar(mval) or mval.size == 1):
        rowchild = row.copy()
        children = row['_children'] = []
        rowchild['name'] = ''
        rowchild['size'] = ''
        dval_flat = dval.flatten()
        mval_flat = mval.flatten()
        scaler_flat = _get_flat(scaler, mval.size)
        adder_flat = _get_flat(adder, mval.size)
        ref_flat = _get_flat(ref, mval.size)
        ref0_flat = _get_flat(ref0, mval.size)
        upper_flat = _get_flat(upper, mval.size)
        lower_flat = _get_flat(lower, mval.size)
        equals_flat = _get_flat(equals, mval.size)

        for i in range(dval.size):
            d = rowchild.copy()
            d['index'] = i
            d['driver_val'] = [dval_flat[i], 1]
            d['model_val'] = [mval_flat[i], 1]
            if scaler_flat is not None:
                d['scaler'] = [scaler_flat[i], 1]
            if adder_flat is not None:
                d['adder'] = [adder_flat[i], 1]
            if ref_flat is not None:
                d['ref'] = [ref_flat[i], 1]
            if ref0_flat is not None:
                d['ref0'] = [ref0_flat[i], 1]
            if upper_flat is not None:
                d['upper'] = [upper_flat[i], 1]
            if lower_flat is not None:
                d['lower'] = [lower_flat[i], 1]
            if equals_flat is not None:
                d['equals'] = [equals_flat[i], 1]
            children.append(d)


def compute_jac_view_info(totals, data, dv_vals, response_vals, coloring):
    rownames = [None] * totals.shape[0]
    colnames = [None] * totals.shape[1]

    start = end = 0
    data['ofslices'] = slices = {}
    for n, v in response_vals.items():
        end += v.size
        slices[n] = [start, end]
        rownames[start:end] = [n] * (end - start)
        start = end

    start = end = 0
    data['wrtslices'] = slices = {}
    for n, v in dv_vals.items():
        end += v.size
        slices[n] = [start, end]
        colnames[start:end] = [n] * (end - start)
        start = end

    norm_mat = np.zeros((len(data['ofslices']), len(data['wrtslices'])))

    for i, of in enumerate(response_vals):
        ofstart, ofend = data['ofslices'][of]
        for j, wrt in enumerate(dv_vals):
            wrtstart, wrtend = data['wrtslices'][wrt]
            norm_mat[i, j] = np.linalg.norm(totals[ofstart:ofend, wrtstart:wrtend])

    def mat_magnitude(mat):
        mag = np.log10(np.abs(mat))
        finite = mag[np.isfinite(mag)]
        max_mag = np.max(finite)
        min_mag = np.min(finite)
        cap = np.abs(min_mag)
        if max_mag > cap:
            cap = max_mag
        mag[np.isinf(mag)] = -cap
        return mag

    var_matrix = mat_magnitude(norm_mat)
    matrix = mat_magnitude(totals)

    if coloring is not None: # factor in the sparsity
        mask = np.ones(totals.shape, dtype=bool)
        mask[coloring._nzrows, coloring._nzcols] = 0
        matrix[mask] = np.inf  # we know matrix cannot contain infs by this point

    nonempty_submats = set()  # submats with any nonzero values

    matlist = [None] * matrix.size
    idx = 0
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            val = matrix[i, j]
            if np.isinf(val):
                val = None
            else:
                nonempty_submats.add((rownames[i], colnames[j]))
            matlist[idx] = [i, j, val]
            idx += 1

    data['mat_list'] = matlist

    varmatlist = [None] * var_matrix.size

    # setup up sparsity of var matrix
    idx = 0
    for i, of in enumerate(data['oflabels']):
        for j, wrt in enumerate(data['wrtlabels']):
            if coloring is not None and (of, wrt) not in nonempty_submats:
                val = None
            else:
                val = var_matrix[i, j]
            varmatlist[idx] = [of, wrt, val]
            idx += 1

    data['var_mat_list'] = varmatlist


def view_driver_scaling(driver, outfile='driver_scaling_report.html', show_browser=True,
                        precision=6, title=None, jac=True):
    """
    Generate a self-contained html file containing a detailed connection viewer.

    Optionally pops up a web browser to view the file.

    Parameters
    ----------
    driver : Driver
        The driver used for the scaling report.

    outfile : str, optional
        The name of the output html file.  Defaults to 'connections.html'.

    show_browser : bool, optional
        If True, pop up a browser to view the generated html file.
        Defaults to True.

    precision : int, optional
        Sets the precision for displaying array values.

    title : str, optional
        Sets the title of the web page.

    jac : bool
        If True, show jacobian information.
    """
    if MPI and MPI.COMM_WORLD.rank != 0:
        return

    dv_table = []
    con_table = []
    obj_table = []

    dv_vals = driver.get_design_var_values(get_remote=True)
    obj_vals = driver.get_objective_values(driver_scaling=True)
    con_vals = driver.get_constraint_values(driver_scaling=True)

    mod_meta = driver._problem().model._var_allprocs_abs2meta['output']

    default = ''

    idx = 1  # unique ID for use by Tabulator

    # set up design vars table data
    for name, meta in driver._designvars.items():
        scaler = meta['total_scaler']
        adder = meta['total_adder']
        ref = meta['ref']
        ref0 = meta['ref0']
        lower = meta['lower']
        upper = meta['upper']

        mval = dv_vals[name]  # dv_vals are unscaled
        dval = _scale(mval, scaler, adder, default)

        dct = {
            'id': idx,
            'name': name,
            'size': meta['size'],
            'driver_val': _getnorm_and_size(dval),
            'driver_units': _getdef(meta['units'], default),
            'model_val': _getnorm_and_size(mval),
            'model_units': _getdef(mod_meta[meta['ivc_source']]['units'], default),
            'ref': _getnorm_and_size(ref, default),
            'ref0': _getnorm_and_size(ref0, default),
            'scaler': _getnorm_and_size(scaler, default),
            'adder': _getnorm_and_size(adder, default),
            'lower': _getnorm_and_size(lower, default),  # scaled
            'upper': _getnorm_and_size(upper, default),  # scaled
            'index': '',
        }

        dv_table.append(dct)

        _add_child_rows(dct, mval, dval, scaler=scaler, adder=adder, ref=ref, ref0=ref0,
                        lower=lower, upper=upper)

        idx += 1

    # set up constraints table data
    for name, meta in driver._cons.items():
        scaler = meta['total_scaler']
        adder = meta['total_adder']
        ref = meta['ref']
        ref0 = meta['ref0']
        lower = meta['lower']
        upper = meta['upper']
        equals = meta['equals']

        dval = con_vals[name]
        mval = _unscale(dval, scaler, adder, default)

        dct = {
            'id': idx,
            'name': name,
            'size': meta['size'],
            'index': '',
            'driver_val': _getnorm_and_size(dval),
            'driver_units': _getdef(meta['units'], default),
            'model_val': _getnorm_and_size(mval),
            'model_units': _getdef(mod_meta[meta['ivc_source']]['units'], default),
            'ref': _getnorm_and_size(meta['ref'], default),
            'ref0': _getnorm_and_size(meta['ref0'], default),
            'scaler': _getnorm_and_size(scaler, default),
            'adder': _getnorm_and_size(adder, default),
            'lower': _getnorm_and_size(meta['lower'], default),  # scaled
            'upper': _getnorm_and_size(meta['upper'], default),  # scaled
            'equals': _getnorm_and_size(meta['equals'], default), # scaled
            'linear': meta['linear'],
        }

        con_table.append(dct)
        _add_child_rows(dct, mval, dval, scaler=scaler, adder=adder, ref=ref, ref0=ref0,
                        lower=lower, upper=upper, equals=equals)

        idx += 1

    # set up objectives table data
    for name, meta in driver._objs.items():
        scaler = meta['total_scaler']
        adder = meta['total_adder']
        ref = meta['ref']
        ref0 = meta['ref0']

        dval = obj_vals[name]
        mval = _unscale(dval, scaler, adder, default)

        dct = {
            'id': idx,
            'name': name,
            'size': meta['size'],
            'index': '',
            'driver_val': _getnorm_and_size(dval),
            'driver_units': _getdef(meta['units'], default),
            'model_val': _getnorm_and_size(mval),
            'model_units': _getdef(mod_meta[meta['ivc_source']]['units'], default),
            'ref': _getnorm_and_size(meta['ref'], default),
            'ref0': _getnorm_and_size(meta['ref0'], default),
            'scaler': _getnorm_and_size(scaler, default),
            'adder': _getnorm_and_size(adder, default),
        }

        obj_table.append(dct)
        _add_child_rows(dct, mval, dval, scaler=scaler, adder=adder, ref=ref, ref0=ref0)

        idx += 1

    data = {
        'title': _getdef(title, ''),
        'dv_table': dv_table,
        'con_table': con_table,
        'obj_table': obj_table,
    }

    if jac:
        coloring = driver._get_static_coloring()
        if coloring_mod._use_total_sparsity and jac:
            if coloring is None and driver._coloring_info['dynamic']:
                coloring = coloring_mod.dynamic_total_coloring(driver)

        # assemble data for jacobian visualization
        data['oflabels'] = driver._get_ordered_nl_responses()
        data['wrtlabels'] = list(dv_vals)

        totals = driver._compute_totals(of=data['oflabels'], wrt=data['wrtlabels'],
                                        return_format='array')

        data['linear'] = lindata = {}
        lindata['oflabels'] = [n for n, meta in driver._cons.items() if meta['linear']]
        lindata['wrtlabels'] = data['wrtlabels']  # needs to mimic data structure

        # check for separation of linear constraints
        if lindata['oflabels']:
            if set(lindata['oflabels']).intersection(data['oflabels']):
                # linear cons are found in data['oflabels'] so they're not separated
                lindata['oflabels'] = []
                lindata['wrtlables'] = []

        # print("var_matrix")
        # print(norm_mat)
        # print("----")
        # print(var_matrix)
        # print("obj", list(obj_vals))
        # print("con", list(con_vals))
        # print("dv", list(dv_vals))

        full_response_vals = con_vals.copy()
        full_response_vals.update(obj_vals)
        response_vals = {n: full_response_vals[n] for n in data['oflabels']}

        compute_jac_view_info(totals, data, dv_vals, response_vals, coloring)
        if lindata['oflabels']:
            lintotals = driver._compute_totals(of=data['oflabels'], wrt=data['wrtlabels'],
                                               return_format='array')
            lin_response_vals = {n: full_response_vals[n] for n in lindata['oflabels']}
            compute_jac_view_info(lintotals, lindata, dv_vals, lin_response_vals, None)

    viewer = 'scaling_table.html'

    code_dir = os.path.dirname(os.path.abspath(__file__))
    libs_dir = os.path.join(code_dir, 'libs')
    style_dir = os.path.join(code_dir, 'style')

    with open(os.path.join(code_dir, viewer), "r") as f:
        template = f.read()

    with open(os.path.join(libs_dir, 'tabulator.min.js'), "r") as f:
        tabulator_src = f.read()

    with open(os.path.join(style_dir, 'tabulator.min.css'), "r") as f:
        tabulator_style = f.read()

    with open(os.path.join(libs_dir, 'd3.v6.min.js'), "r") as f:
        d3_src = f.read()

    jsontxt = json.dumps(data, default=default_noraise)

    with open(outfile, 'w') as f:
        s = template.replace("<tabulator_src>", tabulator_src)
        s = s.replace("<tabulator_style>", tabulator_style)
        s = s.replace("<d3_src>", d3_src)
        s = s.replace("<scaling_data>", jsontxt)
        f.write(s)

    if show_browser:
        webview(outfile)


def _scaling_setup_parser(parser):
    """
    Set up the openmdao subparser for the 'openmdao driver_scaling' command.

    Parameters
    ----------
    parser : argparse subparser
        The parser we're adding options to.
    """
    parser.add_argument('file', nargs=1, help='Python file containing the model.')
    parser.add_argument('-o', default='driver_scaling_report.html', action='store', dest='outfile',
                        help='html output file.')
    parser.add_argument('-t', '--title', action='store', dest='title',
                        help='title of web page.')
    parser.add_argument('--no_browser', action='store_true', dest='no_browser',
                        help="don't display in a browser.")
    parser.add_argument('-p', '--problem', action='store', dest='problem', help='Problem name')
    parser.add_argument('--no-jac', action='store_true', dest='nojac',
                        help="Don't show jacobian info")


def _scaling_cmd(options, user_args):
    """
    Return the post_setup hook function for 'openmdao driver_scaling'.

    Parameters
    ----------
    options : argparse Namespace
        Command line options.
    user_args : list of str
        Args to be passed to the user script.
    """
    def _scaling(problem):
        hooks._unregister_hook('final_setup', 'Problem')  # avoid recursive loop
        driver = problem.driver
        if options.title:
            title = options.title
        else:
            title = "Driver scaling for %s" % os.path.basename(options.file[0])
        view_driver_scaling(driver, outfile=options.outfile, show_browser=not options.no_browser,
                            title=title, jac=not options.nojac)
        exit()

    # register the hook
    hooks._register_hook('final_setup', class_name='Problem', inst_id=options.problem,
                         post=_scaling)

    ignore_errors(True)
    _load_and_exec(options.file[0], user_args)
