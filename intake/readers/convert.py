"""Convert between python representations of data

By convention, functions here do not change the data, just how it is held.
"""
from __future__ import annotations

import re
from itertools import chain

from intake import import_name, conf
from intake.readers import BaseData, BaseReader, readers
from intake.readers.utils import all_to_one, subclasses


class ImportsProperty:
    """Builds the .imports attribute from classes in the .instances class attribute"""

    def __get__(self, obj, cls):
        # this asserts that ALL in and out types should be importable
        cls.imports = set(
            _.split(":", 1)[0].split(".", 1)[0]
            for _ in chain(cls.instances, cls.instances.values())
            if _ is not SameType
        )
        return cls.imports


class BaseConverter(BaseReader):
    """Converts from one object type to another

    Most often, subclasses call a single function on the data, but arbitrary complex transforms
    are possible. This is designed to be one step in a Pipeline.

    .run() will be called on the output object from the previous stage, subclasses will wither
    override that, or just provide a func=.
    """

    instances: dict[str, str] = {}

    def run(self, x, *args, **kwargs):
        func = import_name(self.func)
        return func(x, *args, **kwargs)

    def _read(self, *args, data=None, **kwargs):
        if data is None:
            data = args[0]
            args = args[1:]
        if isinstance(data, BaseReader):
            data = data.read()
        return self.run(data, *args, **kwargs)


class GenericFunc(BaseConverter):
    """Call given arbitrary function

    This could be a transform or anything; the caller should specify what the
    output_instance will be, since the class doesn't know.
    """

    def _read(self, *args, data=None, func=None, data_kwarg=None, **kwargs):
        if data is not None and isinstance(data, BaseReader):
            data = data.read()
        if data is not None:
            if data_kwarg is None:
                return func(data, *args, **kwargs)
            else:
                kwargs[data_kwarg] = data
                return func(*args, **kwargs)
        return func(*args, **kwargs)


class SameType:
    """Used to indicate that the output of a transform is the same as the input, which is arbitrary"""


class DuckToPandas(BaseConverter):
    instances = {"duckdb:DuckDBPyRelation": "pandas:DataFrame"}
    func = "duckdb:DuckDBPyConnection.df"

    def run(self, x, *args, **kwargs):
        return x.df()


class DaskDFToPandas(BaseConverter):
    instances = {
        "dask.dataframe:DataFrame": "pandas:DataFrame",
        "dask.array:Array": "numpy:ndarray",
    }
    func = "dask:compute"


class PandasToGeopandas(BaseConverter):
    instances = {"pandas:DataFrame": "geopandas:GeoDataFrame"}
    func = "geopandas:GeoDataFrame"


class ToHvPlot(BaseConverter):
    instances = all_to_one(
        {
            "pandas:DataFrame",
            "dask.dataframe:DataFrame",
            "xarray:Dataset",
            "xarray:DataArray",
        },
        "holoviews.core.layout:Composable",
    )
    func = "hvplot:hvPlot"

    def run(self, x, explorer: bool = False, **kw):
        """For tabular data only, pass explorer=True to get an interactive GUI"""
        import hvplot

        if explorer:
            # this is actually a hvplot.ui:hvPlotExplorer and only allows tabular data
            return hvplot.explorer(x, **kw)
        return hvplot.hvPlot(x, **kw)()


class RayToPandas(BaseConverter):
    instances = {"ray.data:Dataset": "pandas:DataFrame"}
    func = "ray.data:Dataset.to_pandas"


class PandasToRay(BaseConverter):
    instances = {"pandas:DataFrame": "ray.data:Dataset"}
    func = "ray.data:from_pandas"


class RayToDask(BaseConverter):
    instances = {"ray.data:Dataset": "dask.dataframe:DataFrame"}
    func = "ray.data:Dataset.to_dask"


class DaskToRay(BaseConverter):
    instances = {"dask.dataframe:DataFrame": "ray.data:Dataset"}
    func = "ray.data:from_dask"


class HuggingfaceToRay(BaseConverter):
    instances = {"datasets.arrow_dataset:Dataset": "ray.data:Dataset"}
    func = "ray.data:from_huggingface"


class TorchToRay(BaseConverter):
    instances = {"torch.utils.data:Dataset": "ray.data:Dataset"}
    func = "ray.data:from_torch"


class SparkDFToRay(BaseConverter):
    instances = {"pyspark.sql:DataFrame": "ray.data:Dataset"}
    func = "ray.data:from_spark"


class RayToSpark(BaseConverter):
    instances = {"ray.data:Dataset": "pyspark.sql:DataFrame"}
    func = "ray.data:Dataset.to_spark"


class TiledNodeToCatalog(BaseConverter):
    instances = {"tiled.client.node:Node": "intake.readers.entry:Catalog"}

    def run(self, x, **kw):
        # eager creation of entries from a node
        from intake.readers.datatypes import TiledDataset, TiledService
        from intake.readers.entry import Catalog
        from intake.readers.readers import TiledClient, TiledNode

        cat = Catalog()
        for k, client in x.items():
            if type(client).__name__ == "Node":
                data = TiledService(url=client.uri)
                reader = TiledNode(data=data, metadata=client.item)
                cat[k] = reader
            else:
                data = TiledDataset(url=client.uri)
                reader = TiledClient(
                    data,
                    output_instance=f"{type(client).__module__}:{type(client).__name__}",
                    metadata=client.item,
                )
                cat[k] = reader
        return cat


class TiledSearch(BaseConverter):
    """See https://blueskyproject.io/tiled/tutorials/search.html"""

    instances = {"tiled.client.node:Node": "tiled.client.node:Node"}

    def run(self, x, *arg, **kw):
        # TODO: expects instances of classes in tiled.queries, which must be pickled, but
        #  could allow (name, args) or something else
        return x.search(*arg, **kw)


class DeltaQueryToDask(BaseConverter):
    instances = {"deltalake:DeltaTable": "dask.dataframe:DataFrame"}
    func = "deltalake:DeltaTable.file_uris"

    def _read(self, reader, query, *args, **kwargs):
        import dask.dataframe as dd

        file_uris = reader.read().file_uris(query)

        return dd.read_parquet(file_uris, storage_options=reader.kwargs["data"].storage_options)


class DeltaQueryToDaskGeopandas(BaseConverter):
    instances = {"deltalake:DeltaTable": "dask_geopandas:GeoDataFrame"}
    func = "deltalake:DeltaTable.file_uris"

    def _read(self, reader, query, *args, **kwargs):
        import dask_geopandas

        file_uris = reader.read().file_uris(query)

        return dask_geopandas.read_parquet(
            file_uris, storage_options=reader.kwargs["data"].storage_options
        )


class PandasToMetagraph(BaseConverter):
    instances = {"pd:DataFrame": "metagraph.wrappers.EdgeSet:PandasEdgeSet"}
    func = "metagraph.wrappers.EdgeSet:PandasEdgeSet"


class NibabelToNumpy(BaseConverter):
    instances = {"nibabel.spatialimages:SpatialImage": "numpy:ndimage"}
    func = "nibabel.spatialimages:SpatialImage.get_fdata"


class DicomToNumpy(BaseConverter):
    instances = {"pydicom.dataset:FileDataset": "numpy:ndarray"}
    func = "pydicom.dataset:FileDataset.pixel_array"

    def run(self, x, *args, **kwargs):
        return x.pixel_array


def convert_class(data, out_type: str):
    """Get conversion class from given data to out_type

    This works on concrete data, not a datatype or reader instance. It returns the
    first match. out_type will match on regex, e.g., "pandas" would match "pandas:DataFrame"
    """
    package = type(data).__module__.split(".", 1)[0]
    for cls in subclasses(BaseConverter):
        for intype, outtype in cls.instances.items():
            if not re.findall(out_type, outtype):
                continue
            if intype.split(".", 1)[0] != package:
                continue
            thing = readers.import_name(intype)
            if isinstance(data, thing):
                return cls
    raise ValueError("Converter not found")


def convert_classes(in_type: str):
    """Get available conversion classes for input type"""
    out_dict = {}
    package = in_type.split(":", 1)[0].split(".", 1)[0]
    for cls in subclasses(BaseConverter):
        for intype, outtype in cls.instances.items():
            if intype.split(":", 1)[0].split(".", 1)[0] != package:
                continue
            if re.findall(intype.lower(), in_type.lower()) or re.findall(
                in_type.lower(), intype.lower()
            ):
                if outtype == SameType:
                    outtype = intype
                out_dict[outtype] = cls
    return out_dict


class Pipeline(readers.BaseReader):
    """Holds a list of transforms/conversions to be enacted in sequence

    A transform on a pipeline makes a new pipeline with that transform added to the sequence
    of operations.
    """

    from intake.readers.readers import BaseReader

    def __init__(
        self,
        steps: list[tuple[BaseReader, tuple, dict]],
        out_instances: list[str],
        output_instance=None,
        metadata=None,
        **kwargs,
    ):
        self.output_instances = []
        prev = out_instances[0]
        for inst in out_instances:
            if inst is SameType:
                inst = prev
            prev = inst
            self.output_instances.append(inst)
        super().__init__(
            output_instance=output_instance or self.output_instances[-1],
            metadata=metadata,
            steps=steps,
            out_instances=self.output_instances,
        )
        steps[-1][2].update(kwargs)

    @property
    def steps(self):
        return self.kwargs["steps"]

    def __call__(self, *args, **kwargs):
        return super().__call__(
            *args,
            steps=self.steps,
            out_instances=self.output_instances,
            metadata=self.metadata,
            **kwargs,
        )

    def __repr__(self):
        start = "PipelineReader: \n"
        bits = [
            f"  {i}: {f.qname() if isinstance(f, BaseReader) else f.__name__}, {args} {kw} => {out}"
            for i, ((f, args, kw), out) in enumerate(zip(self.steps, self.output_instances))
        ]
        return "\n".join([start] + bits)

    @property
    def tokens(self):
        return [(self.token, n) for n in range(len(self.steps))]

    def output_doc(self):
        from intake import import_name

        out = import_name(self.output_instance)
        return out.__doc__

    def doc(self):
        return self.doc_n(-1)

    def doc_n(self, n):
        return self.steps[n][0].doc()

    def _read_stage_n(self, stage, discover=False, **kwargs):
        from intake.readers.readers import BaseReader

        func, arg, kw = self.steps[stage]

        kw2 = kw.copy()
        kw2.update(kwargs)
        for k, v in kw.items():
            if isinstance(v, BaseReader):
                kw2[k] = v.read()
            else:
                kw2[k] = v
        arg = kw2.pop("args", arg)
        # TODO: these conditions can probably be combined
        if isinstance(func, type) and issubclass(func, BaseReader):
            if discover:
                return func().discover(*arg, **kw2)
            else:
                return func().read(*arg, **kw2)
        elif isinstance(func, BaseReader):
            if discover:
                return func.discover(*arg, **kw2)
            else:
                return func.read(*arg, **kw2)
        else:
            return func(*arg, **kw2)

    def _read(self, discover=False, **kwargs):
        data = None
        for i, step in enumerate(self.steps):
            kw = kwargs if i == len(self.steps) else {}
            if i:
                data = self._read_stage_n(i, data=data, **kw)
            else:
                data = self._read_stage_n(i, discover=discover, **kw)
        return data

    def apply(self, func, *arg, output_instance=None, **kwargs):
        """Add a pipeline stage applying function to the pipeline output so far"""
        from intake.readers.convert import GenericFunc

        kwargs["func"] = func
        return self.with_step((GenericFunc, arg, kwargs), output_instance or self.output_instance)

    def first_n_stages(self, n: int):
        """Truncate pipeline to the given stage

        If n is equal to the number of steps, this is a simple copy.
        """
        # TODO: allow n=0 to get the basic reader?
        if n < 1 or n > len(self.steps):
            raise ValueError(f"n must be between {1} and {len(self.steps)}")

        pipe = Pipeline(
            steps=self.steps[:n], out_instances=self.output_instances[:n], **self.kwargs
        )
        if n < len(self.steps):
            pipe.token = (self.token, n)
        return pipe

    def discover(self, **kwargs):
        return self.read(discover=True)

    def with_step(self, step, out_instance):
        if not isinstance(step, tuple):
            # must be a func - check?
            step = (step, (), {})
        return Pipeline(
            steps=self.steps + [step],
            out_instances=self.output_instances + [out_instance],
        )


def conversions_graph(avoid=None):
    avoid = avoid or conf["reader_avoid"]
    if isinstance(avoid, str):
        avoid = [avoid]
    import networkx

    from intake.readers.utils import subclasses

    graph = networkx.DiGraph()

    # transformers
    nodes = set(
        cls.output_instance
        for cls in subclasses(readers.BaseReader)
        if cls.output_instance and not any(re.findall(_, cls.qname()) for _ in avoid)
    )
    graph.add_nodes_from(nodes)

    for cls in subclasses(readers.BaseReader):
        if any(re.findall(_, cls.qname()) for _ in avoid):
            continue
        if cls.output_instance:
            for impl in cls.implements:
                graph.add_node(cls.output_instance)
                graph.add_edge(impl.qname(), cls.output_instance, label=cls.qname())
    for cls in subclasses(BaseConverter):
        if any(re.findall(_, cls.qname()) for _ in avoid):
            continue
        for inttype, outtype in cls.instances.items():
            if inttype != ".*" and inttype != outtype:
                graph.add_nodes_from((inttype, outtype))
                graph.add_edge(inttype, outtype, label=cls.qname())

    return graph


def plot_conversion_graph(filename) -> None:
    # TODO: return a PNG datatype or something else?
    import networkx as nx

    g = conversions_graph()
    a = nx.nx_agraph.to_agraph(g)  # requires pygraphviz
    a.draw(filename, prog="fdp")


def path(start: str, end: str, cutoff: int = 5, avoid=None) -> list:
    import networkx as nx

    g = conversions_graph(avoid=avoid)
    alltypes = list(g)
    matchtypes = [_ for _ in alltypes if re.findall(start, _)]
    if not matchtypes:
        raise ValueError("type found no match: %s", start)
    start = matchtypes[0]
    matchtypes = [_ for _ in alltypes if re.findall(end, _)]
    if not matchtypes:
        raise ValueError("outtype found no match: %s", end)
    end = matchtypes[0]
    return sorted(nx.all_simple_edge_paths(g, start, end, cutoff=cutoff), key=len)


def auto_pipeline(
    url: str | BaseData,
    outtype: str,
    storage_options: dict | None = None,
    avoid: list[str] | None = None,
) -> Pipeline:
    """Create pipeline from given URL to desired output type

    Will search for the shortest conversion path from the inferred data-type to the
    output.

    Parameters
    ----------
    url: input data, usually a location/URL, but maybe a data instance
    outtype: pattern to match to possible output types
    storage_options: if url is a remote str, these are kwargs that fsspec may need to
        access it
    avoid: don't consider readers whose names match any of these strings
    """
    from intake.readers.datatypes import recommend

    if isinstance(url, str):
        if storage_options:
            data = recommend(url, storage_options=storage_options)[0](
                url=url, storage_options=storage_options
            )
        else:
            data = recommend(url)[0](url=url)
    else:
        data = url
    if isinstance(data, BaseData):
        start = data.qname()
        steps = path(start, outtype, avoid=avoid)
        reader = data.to_reader(outtype=steps[0][0][1])
        for s in steps[0][1:]:
            reader = reader.transform[s[1]]
    elif isinstance(data, BaseReader):
        reader = data
        steps = path(data.output_instance, outtype, avoid=avoid)
        for s in steps[0]:
            reader = reader.transform[s[1]]

    return reader
