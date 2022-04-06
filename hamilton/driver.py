import logging
from datetime import datetime
from typing import Dict, Collection, List, Any
from types import ModuleType

import pandas as pd

# required if we want to run this code stand alone.
import typing

from dataclasses import dataclass

from hamilton import node

DISCORD_ERROR_MESSAGE = (
    '-------------------------------------------------------------------\n'
    'Oh no an error! Need help with Hamilton?\n'
    'Join our discord and ask for help! https://discord.gg/wCqxqBqn73\n'
    '-------------------------------------------------------------------\n'
)

if __name__ == '__main__':
    import graph
    import node
else:
    from . import graph, base
    from . import node

logger = logging.getLogger(__name__)


@dataclass
class Variable:
    """External facing API for hamilton. Having this as a dataclass allows us
    to hide the internals of the system but expose what the user might need.
    Furthermore, we can always add attributes and maintain backwards compatibility."""
    name: str
    type: typing.Type


class Driver(object):
    """This class orchestrates creating and executing the DAG to create a dataframe."""

    def __init__(self, config: Dict[str, Any], *modules: ModuleType, adapter: base.HamiltonGraphAdapter = None):
        """Constructor: creates a DAG given the configuration & modules to crawl.

        :param config: This is a dictionary of initial data & configuration.
                       The contents are used to help create the DAG.
        :param modules: Python module objects you want to inspect for Hamilton Functions.
        :param adapter: Optional. A way to wire in another way of "executing" a hamilton graph.
                        Defaults to using original Hamilton adapter which is single threaded in memory python.
        """
        if adapter is None:
            adapter = base.SimplePythonDataFrameGraphAdapter()
        try:
            self.graph = graph.FunctionGraph(*modules, config=config, adapter=adapter)
        except Exception as e:
            logger.error(DISCORD_ERROR_MESSAGE)
            raise e
        self.adapter = adapter

    def _node_is_required_by_anything(self, node_: node.Node) -> bool:
        """Checks dependencies on this node and determines if at least one requires it.

        Nodes can be optionally depended upon, i.e. the function parameter has a default value. We want to check that
        of the nodes the depend on this one, at least one of them requires it, i.e. the parameter is not optional.

        :param node_: node in question
        :return: True if it is required by any downstream node, false otherwise
        """
        required = False
        for downstream_node in node_.depended_on_by:
            _, dep_type = downstream_node.input_types[node_.name]
            if dep_type == node.DependencyType.REQUIRED:
                return True
        return required

    def validate_inputs(self, user_nodes: Collection[node.Node], inputs: typing.Optional[Dict[str, Any]] = None):
        """Validates that inputs meet our expectations. This means that:
        1. The runtime inputs don't clash with the graph's config
        2. All expected graph inputs are provided, either in config or at runtime

        :param user_nodes: The required nodes we need for computation.
        :param inputs: the user inputs provided.
        """
        if inputs is None:
            inputs = {}
        all_inputs, = graph.FunctionGraph.combine_config_and_inputs(self.graph.config, inputs),
        errors = []
        for user_node in user_nodes:
            if user_node.name not in all_inputs:
                if self._node_is_required_by_anything(user_node):
                    errors.append(f'Error: Required input {user_node.name} not provided '
                                  f'for nodes: {[node.name for node in user_node.depended_on_by]}.')
            elif (all_inputs[user_node.name] is not None
                  and not self.adapter.check_input_type(user_node.type, all_inputs[user_node.name])):
                errors.append(f'Error: Type requirement mismatch. Expected {user_node.name}:{user_node.type} '
                              f'got {all_inputs[user_node.name]} instead.')
        if errors:
            errors.sort()
            error_str = f'{len(errors)} errors encountered:\n  ' + '\n  '.join(errors)
            raise ValueError(error_str)

    def execute(self,
                final_vars: List[str],
                overrides: Dict[str, Any] = None,
                display_graph: bool = False,
                inputs: Dict[str, Any] = None,
                ) -> pd.DataFrame:
        """Executes computation.

        :param final_vars: the final list of variables we want in the data frame.
        :param overrides: the user defined input variables.
        :param display_graph: DEPRECATED. Whether we want to display the graph being computed.
        :param inputs: Runtime inputs to the DAG.
        :return: a data frame consisting of the variables requested.
        """
        if display_graph:
            logger.warning('display_graph=True is deprecated. It will be removed in the 2.0.0 release. '
                           'Please use visualize_execution().')
        try:
            outputs = self.raw_execute(final_vars, overrides, display_graph, inputs=inputs)
            return self.adapter.build_result(**outputs)
        except Exception as e:
            logger.error(DISCORD_ERROR_MESSAGE)
            raise e

    def raw_execute(self,
                    final_vars: List[str],
                    overrides: Dict[str, Any] = None,
                    display_graph: bool = False,
                    inputs: Dict[str, Any] = None) -> Dict[str, Any]:
        """Raw execute function that does the meat of execute.

        It does not try to stitch anything together. Thus allowing wrapper executes around this to shape the output
        of the data.

        :param final_vars: Final variables to compute
        :param overrides: Overrides to run.
        :param display_graph: DEPRECATED. DO NOT USE. Whether or not to display the graph when running it
        :param inputs: Runtime inputs to the DAG
        :return:
        """
        nodes, user_nodes = self.graph.get_upstream_nodes(final_vars, inputs)
        self.validate_inputs(user_nodes, inputs)  # TODO -- validate within the function graph itself
        if display_graph:  # deprecated flow.
            logger.warning('display_graph=True is deprecated. It will be removed in the 2.0.0 release. '
                           'Please use visualize_execution().')
            self.visualize_execution(final_vars, 'test-output/execute.gv', {'view': True})
            if self.has_cycles(final_vars):  # here for backwards compatible driver behavior.
                raise ValueError('Error: cycles detected in you graph.')
        memoized_computation = dict()  # memoized storage
        self.graph.execute(nodes, memoized_computation, overrides, inputs)
        outputs = {c: memoized_computation[c] for c in final_vars}  # only want request variables in df.
        del memoized_computation  # trying to cleanup some memory
        return outputs

    def list_available_variables(self) -> List[Variable]:
        """Returns available variables.

        :return: list of available variables.
        """
        return [Variable(node.name, node.type) for node in self.graph.get_nodes()]

    def display_all_functions(self, output_file_path: str, render_kwargs: dict = None):
        """Displays the graph of all functions loaded!

        :param output_file_path: the full URI of path + file name to save the dot file to.
            E.g. 'some/path/graph-all.dot'
        :param render_kwargs: a dictionary of values we'll pass to graphviz render function. Defaults to viewing.
            If you do not want to view the file, pass in `{'view':False}`.
        """
        try:
            self.graph.display_all(output_file_path, render_kwargs)
        except ImportError as e:
            logger.warning(f'Unable to import {e}', exc_info=True)

    def visualize_execution(self,
                            final_vars: List[str],
                            output_file_path: str,
                            render_kwargs: dict,
                            inputs: Dict[str, Any] = None):
        """Visualizes Execution.

        Note: overrides are not handled at this time.

        :param final_vars: the outputs we want to compute.
        :param output_file_path: the full URI of path + file name to save the dot file to.
            E.g. 'some/path/graph.dot'
        :param render_kwargs: a dictionary of values we'll pass to graphviz render function. Defaults to viewing.
            If you do not want to view the file, pass in `{'view':False}`.
        :param inputs: Optional. Runtime inputs to the DAG.
        """
        nodes, user_nodes = self.graph.get_upstream_nodes(final_vars, inputs)
        self.validate_inputs(user_nodes, inputs)
        try:
            self.graph.display(nodes, user_nodes, output_file_path, render_kwargs=render_kwargs)
        except ImportError as e:
            logger.warning(f'Unable to import {e}', exc_info=True)

    def has_cycles(self, final_vars: List[str]) -> bool:
        """Checks that the created graph does not have cycles.

        :param final_vars: the outputs we want to compute.
        :return: boolean True for cycles, False for no cycles.
        """
        # get graph we'd be executing over
        nodes, user_nodes = self.graph.get_upstream_nodes(final_vars)
        return self.graph.has_cycles(nodes, user_nodes)


if __name__ == '__main__':
    """some example test code"""
    import sys
    import importlib

    formatter = logging.Formatter('[%(levelname)s] %(asctime)s %(name)s(%(lineno)s): %(message)s')
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.setLevel(logging.INFO)

    if len(sys.argv) < 2:
        logger.error('No modules passed')
        sys.exit(1)
    logger.info(f'Importing {sys.argv[1]}')
    module = importlib.import_module(sys.argv[1])

    x = pd.date_range('2019-01-05', '2020-12-31', freq='7D')
    x.index = x

    dr = Driver({
        'VERSION': 'kids', 'as_of': datetime.strptime('2019-06-01', '%Y-%m-%d'),
        'end_date': '2020-12-31', 'start_date': '2019-01-05',
        'start_date_d': datetime.strptime('2019-01-05', '%Y-%m-%d'),
        'end_date_d': datetime.strptime('2020-12-31', '%Y-%m-%d'),
        'segment_filters': {'business_line': 'womens'}
    }, module)
    df = dr.execute(
        [
            'date_index',
            'some_column'
        ]
        # ,overrides={'DATE': pd.Series(0)}
        , display_graph=False)
    print(df)