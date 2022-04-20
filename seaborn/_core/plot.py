from __future__ import annotations

import io
import os
import re
import sys
import itertools
from collections import abc
from collections.abc import Callable, Generator, Hashable
from typing import Any

import pandas as pd
from pandas import DataFrame, Series, Index
import matplotlib as mpl
from matplotlib.axes import Axes
from matplotlib.artist import Artist
from matplotlib.figure import Figure

from seaborn._marks.base import Mark
from seaborn._stats.base import Stat
from seaborn._core.data import PlotData
from seaborn._core.moves import Move
from seaborn._core.scales import ScaleSpec, Scale
from seaborn._core.subplots import Subplots
from seaborn._core.groupby import GroupBy
from seaborn._core.properties import PROPERTIES, Property, Coordinate
from seaborn._core.typing import DataSource, VariableSpec, OrderSpec
from seaborn._core.rules import categorical_order
from seaborn._compat import set_scale_obj
from seaborn.external.version import Version

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from matplotlib.figure import SubFigure


if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


class Layer(TypedDict, total=False):

    mark: Mark  # TODO allow list?
    stat: Stat | None  # TODO allow list?
    move: Move | list[Move] | None
    data: PlotData
    source: DataSource
    vars: dict[str, VariableSpec]
    orient: str


class FacetSpec(TypedDict, total=False):

    variables: dict[str, VariableSpec]
    structure: dict[str, list[str]]
    wrap: int | None


class PairSpec(TypedDict, total=False):

    variables: dict[str, VariableSpec]
    structure: dict[str, list[str]]
    cartesian: bool
    wrap: int | None


class Plot:

    # TODO use TypedDict throughout?

    _data: PlotData
    _layers: list[Layer]
    _scales: dict[str, ScaleSpec]

    _subplot_spec: dict[str, Any]  # TODO values type
    _facet_spec: FacetSpec
    _pair_spec: PairSpec

    def __init__(
        self,
        # TODO rewrite with overload to clarify possible signatures?
        *args: DataSource | VariableSpec,
        data: DataSource = None,
        x: VariableSpec = None,
        y: VariableSpec = None,
        # TODO maybe enumerate variables for tab-completion/discoverability?
        # I think the main concern was being extensible ... possible to add
        # to the signature using inspect?
        **variables: VariableSpec,
    ):

        if args:
            data, x, y = self._resolve_positionals(args, data, x, y)

        # Build new dict with x/y rather than adding to preserve natural order
        if y is not None:
            variables = {"y": y, **variables}
        if x is not None:
            variables = {"x": x, **variables}

        self._data = PlotData(data, variables)
        self._layers = []
        self._scales = {}

        self._subplot_spec = {}
        self._facet_spec = {}
        self._pair_spec = {}

        self._target = None

        # TODO
        self._inplace = False

    def _resolve_positionals(
        self,
        args: tuple[DataSource | VariableSpec, ...],
        data: DataSource, x: VariableSpec, y: VariableSpec,
    ) -> tuple[DataSource, VariableSpec, VariableSpec]:

        if len(args) > 3:
            err = "Plot accepts no more than 3 positional arguments (data, x, y)"
            raise TypeError(err)  # TODO PlotSpecError?
        elif len(args) == 3:
            data_, x_, y_ = args
        else:
            # TODO need some clearer way to differentiate data / vector here
            # Alternatively, could decide this is too flexible for its own good,
            # and require data to be in positional signature. I'm conflicted.
            have_data = isinstance(args[0], (abc.Mapping, pd.DataFrame))
            if len(args) == 2:
                if have_data:
                    data_, x_ = args
                    y_ = None
                else:
                    data_ = None
                    x_, y_ = args
            else:
                y_ = None
                if have_data:
                    data_ = args[0]
                    x_ = None
                else:
                    data_ = None
                    x_ = args[0]

        out = []
        for var, named, pos in zip(["data", "x", "y"], [data, x, y], [data_, x_, y_]):
            if pos is None:
                val = named
            else:
                if named is not None:
                    raise TypeError(f"`{var}` given by both name and position")
                val = pos
            out.append(val)
        data, x, y = out

        return data, x, y

    def __add__(self, other):

        # TODO restrict to Mark / Stat etc?
        raise TypeError("Sorry, this isn't ggplot! Perhaps try Plot.add?")

    def _repr_png_(self) -> tuple[bytes, dict[str, float]]:

        return self.plot()._repr_png_()

    # TODO _repr_svg_?

    def _clone(self) -> Plot:

        if self._inplace:
            return self

        new = Plot()

        # TODO any way to make sure data does not get mutated?
        new._data = self._data

        new._layers.extend(self._layers)
        new._scales.update(self._scales)

        new._subplot_spec.update(self._subplot_spec)
        new._facet_spec.update(self._facet_spec)
        new._pair_spec.update(self._pair_spec)

        new._target = self._target

        return new

    @property
    def _variables(self) -> list[str]:

        variables = (
            list(self._data.frame)
            + list(self._pair_spec.get("variables", []))
            + list(self._facet_spec.get("variables", []))
        )
        for layer in self._layers:
            variables.extend(c for c in layer["vars"] if c not in variables)
        return variables

    def inplace(self, val: bool | None = None) -> Plot:

        # TODO I am not convinced we need this

        if val is None:
            self._inplace = not self._inplace
        else:
            self._inplace = val
        return self

    def on(self, target: Axes | SubFigure | Figure) -> Plot:

        # TODO alternate name: target?

        accepted_types: tuple  # Allow tuple of various length
        if hasattr(mpl.figure, "SubFigure"):  # Added in mpl 3.4
            accepted_types = (
                mpl.axes.Axes, mpl.figure.SubFigure, mpl.figure.Figure
            )
            accepted_types_str = (
                f"{mpl.axes.Axes}, {mpl.figure.SubFigure}, or {mpl.figure.Figure}"
            )
        else:
            accepted_types = mpl.axes.Axes, mpl.figure.Figure
            accepted_types_str = f"{mpl.axes.Axes} or {mpl.figure.Figure}"

        if not isinstance(target, accepted_types):
            err = (
                f"The `Plot.on` target must be an instance of {accepted_types_str}. "
                f"You passed an instance of {target.__class__} instead."
            )
            raise TypeError(err)

        new = self._clone()
        new._target = target

        return new

    def add(
        self,
        mark: Mark,
        stat: Stat | None = None,
        move: Move | None = None,
        orient: str | None = None,
        data: DataSource = None,
        **variables: VariableSpec,
    ) -> Plot:

        # TODO do a check here that mark has been initialized,
        # otherwise errors will be inscrutable

        # TODO decide how to allow Mark to have Stat/Move
        # if stat is None and hasattr(mark, "default_stat"):
        #     stat = mark.default_stat()

        # TODO if data is supplied it overrides the global data object
        # Another option would be to left join (layer_data, global_data)
        # after dropping the column intersection from global_data
        # (but join on what? always the index? that could get tricky...)

        # TODO accept arbitrary variables defined by the stat (/move?) here
        # (but not in the Plot constructor)
        # Should stat variables every go in the constructor, or just in the add call?

        new = self._clone()
        new._layers.append({
            "mark": mark,
            "stat": stat,
            "move": move,
            "vars": variables,
            "source": data,
            "orient": {"v": "x", "h": "y"}.get(orient, orient),  # type: ignore
        })

        return new

    def pair(
        self,
        x: list[Hashable] | Index[Hashable] | None = None,
        y: list[Hashable] | Index[Hashable] | None = None,
        wrap: int | None = None,
        cartesian: bool = True,  # TODO bikeshed name, maybe cross?
        # TODO other existing PairGrid things like corner?
        # TODO transpose, so that e.g. multiple y axes go across the columns
    ) -> Plot:

        # TODO Problems to solve:
        #
        # - Unclear is how to handle the diagonal plots that PairGrid offers
        #
        # - Implementing this will require lots of downscale changes in figure setup,
        #   and especially the axis scaling, which will need to be pair specific

        # TODO lists of vectors currently work, but I'm not sure where best to test

        # TODO is is weird to call .pair() to create univariate plots?
        # i.e. Plot(data).pair(x=[...]). The basic logic is fine.
        # But maybe a different verb (e.g. Plot.spread) would be more clear?
        # Then Plot(data).pair(x=[...]) would show the given x vars vs all.

        # TODO would like to add transpose=True, which would then draw
        # Plot(x=...).pair(y=[...]) across the rows

        pair_spec: PairSpec = {}

        if x is None and y is None:

            # Default to using all columns in the input source data, aside from
            # those that were assigned to a variable in the constructor
            # TODO Do we want to allow additional filtering by variable type?
            # (Possibly even default to using only numeric columns)

            if self._data.source_data is None:
                err = "You must pass `data` in the constructor to use default pairing."
                raise RuntimeError(err)

            all_unused_columns = [
                key for key in self._data.source_data
                if key not in self._data.names.values()
            ]
            if "x" not in self._data:
                x = all_unused_columns
            if "y" not in self._data:
                y = all_unused_columns

        axes = {"x": [] if x is None else x, "y": [] if y is None else y}
        for axis, arg in axes.items():
            if isinstance(arg, (str, int)):
                err = f"You must pass a sequence of variable keys to `{axis}`"
                raise TypeError(err)

        pair_spec["variables"] = {}
        pair_spec["structure"] = {}

        for axis in "xy":
            keys = []
            for i, col in enumerate(axes[axis]):
                key = f"{axis}{i}"
                keys.append(key)
                pair_spec["variables"][key] = col

            if keys:
                pair_spec["structure"][axis] = keys

        # TODO raise here if cartesian is False and len(x) != len(y)?
        pair_spec["cartesian"] = cartesian
        pair_spec["wrap"] = wrap

        new = self._clone()
        new._pair_spec.update(pair_spec)
        return new

    def facet(
        self,
        # TODO require kwargs?
        col: VariableSpec = None,
        row: VariableSpec = None,
        order: OrderSpec | dict[str, OrderSpec] = None,
        wrap: int | None = None,
    ) -> Plot:

        variables = {}
        if col is not None:
            variables["col"] = col
        if row is not None:
            variables["row"] = row

        structure = {}
        if isinstance(order, dict):
            for dim in ["col", "row"]:
                dim_order = order.get(dim)
                if dim_order is not None:
                    structure[dim] = list(dim_order)
        elif order is not None:
            if col is not None and row is not None:
                err = " ".join([
                    "When faceting on both col= and row=, passing `order` as a list"
                    "is ambiguous. Use a dict with 'col' and/or 'row' keys instead."
                ])
                raise RuntimeError(err)
            elif col is not None:
                structure["col"] = list(order)
            elif row is not None:
                structure["row"] = list(order)

        spec: FacetSpec = {
            "variables": variables,
            "structure": structure,
            "wrap": wrap,
        }

        new = self._clone()
        new._facet_spec.update(spec)

        return new

    # TODO def twin()?

    def scale(self, **scales: ScaleSpec) -> Plot:

        new = self._clone()
        new._scales.update(**scales)
        return new

    def configure(
        self,
        figsize: tuple[float, float] | None = None,
        sharex: bool | str | None = None,
        sharey: bool | str | None = None,
    ) -> Plot:

        # TODO add an "auto" mode for figsize that roughly scales with the rcParams
        # figsize (so that works), but expands to prevent subplots from being squished
        # Also should we have height=, aspect=, exclusive with figsize? Or working
        # with figsize when only one is defined?

        new = self._clone()

        # TODO this is a hack; make a proper figure spec object
        new._figsize = figsize  # type: ignore

        if sharex is not None:
            new._subplot_spec["sharex"] = sharex
        if sharey is not None:
            new._subplot_spec["sharey"] = sharey

        return new

    # TODO def legend (ugh)

    def theme(self) -> Plot:

        # TODO Plot-specific themes using the seaborn theming system
        raise NotImplementedError
        new = self._clone()
        return new

    # TODO decorate? (or similar, for various texts) alt names: label?

    def save(self, fname, **kwargs) -> Plot:
        # TODO kws?
        self.plot().save(fname, **kwargs)
        return self

    def plot(self, pyplot=False) -> Plotter:

        # TODO if we have _target object, pyplot should be determined by whether it
        # is hooked into the pyplot state machine (how do we check?)

        plotter = Plotter(pyplot=pyplot)

        common, layers = plotter._extract_data(self)
        plotter._setup_figure(self, common, layers)
        plotter._transform_coords(self, common, layers)

        plotter._compute_stats(self, layers)
        plotter._setup_scales(self, layers)

        # TODO Remove these after updating other methods
        # ---- Maybe have debug= param that attaches these when True?
        plotter._data = common
        plotter._layers = layers

        # plotter._move_marks(self)  # TODO just do this as part of _plot_layer?

        for layer in layers:
            plotter._plot_layer(self, layer)

        # TODO should this go here?
        plotter._make_legend()  # TODO does this return?

        # TODO this should be configurable
        if not plotter._figure.get_constrained_layout():
            plotter._figure.set_tight_layout(True)

        return plotter

    def show(self, **kwargs) -> None:

        # TODO make pyplot configurable at the class level, and when not using,
        # import IPython.display and call on self to populate cell output?

        # Keep an eye on whether matplotlib implements "attaching" an existing
        # figure to pyplot: https://github.com/matplotlib/matplotlib/pull/14024

        self.plot(pyplot=True).show(**kwargs)

    # TODO? Have this print a textual summary of how the plot is defined?
    # Could be nice to stick in the middle of a pipeline for debugging
    # def tell(self) -> Plot:
    #    return self


class Plotter:

    # TODO decide if we ever want these (Plot.plot(debug=True))?
    _data: PlotData
    _layers: list[Layer]
    _figure: Figure

    def __init__(self, pyplot=False):

        self.pyplot = pyplot
        self._legend_contents: list[
            tuple[str, str | int], list[Artist], list[str],
        ] = []
        self._scales: dict[str, Scale] = {}

    def save(self, fname, **kwargs) -> Plotter:
        kwargs.setdefault("dpi", 96)
        self._figure.savefig(os.path.expanduser(fname), **kwargs)
        return self

    def show(self, **kwargs) -> None:
        # TODO if we did not create the Plotter with pyplot, is it possible to do this?
        # If not we should clearly raise.
        import matplotlib.pyplot as plt
        plt.show(**kwargs)

    # TODO API for accessing the underlying matplotlib objects
    # TODO what else is useful in the public API for this class?

    def _repr_png_(self) -> tuple[bytes, dict[str, float]]:

        # TODO better to do this through a Jupyter hook? e.g.
        # ipy = IPython.core.formatters.get_ipython()
        # fmt = ipy.display_formatter.formatters["text/html"]
        # fmt.for_type(Plot, ...)
        # Would like to have a svg option too, not sure how to make that flexible

        # TODO use matplotlib backend directly instead of going through savefig?

        # TODO perhaps have self.show() flip a switch to disable this, so that
        # user does not end up with two versions of the figure in the output

        # TODO use bbox_inches="tight" like the inline backend?
        # pro: better results,  con: (sometimes) confusing results
        # Better solution would be to default (with option to change)
        # to using constrained/tight layout.

        # TODO need to decide what the right default behavior here is:
        # - Use dpi=72 to match default InlineBackend figure size?
        # - Accept a generic "scaling" somewhere and scale DPI from that,
        #   either with 1x -> 72 or 1x -> 96 and the default scaling be .75?
        # - Listen to rcParams? InlineBackend behavior makes that so complicated :(
        # - Do we ever want to *not* use retina mode at this point?
        dpi = 96
        buffer = io.BytesIO()
        self._figure.savefig(buffer, dpi=dpi * 2, format="png", bbox_inches="tight")
        data = buffer.getvalue()

        scaling = .85
        w, h = self._figure.get_size_inches()
        metadata = {"width": w * dpi * scaling, "height": h * dpi * scaling}
        return data, metadata

    def _extract_data(self, p: Plot) -> tuple[PlotData, list[Layer]]:

        common_data = (
            p._data
            .join(None, p._facet_spec.get("variables"))
            .join(None, p._pair_spec.get("variables"))
        )

        layers: list[Layer] = []
        for layer in p._layers:
            spec = layer.copy()
            spec["data"] = common_data.join(layer.get("source"), layer.get("vars"))
            layers.append(spec)

        return common_data, layers

    def _setup_figure(self, p: Plot, common: PlotData, layers: list[Layer]) -> None:

        # --- Parsing the faceting/pairing parameterization to specify figure grid

        # TODO use context manager with theme that has been set
        # TODO (maybe wrap THIS function with context manager; would be cleaner)

        subplot_spec = p._subplot_spec.copy()
        facet_spec = p._facet_spec.copy()
        pair_spec = p._pair_spec.copy()

        for dim in ["col", "row"]:
            if dim in common.frame and dim not in facet_spec["structure"]:
                order = categorical_order(common.frame[dim])
                facet_spec["structure"][dim] = order

        self._subplots = subplots = Subplots(subplot_spec, facet_spec, pair_spec)

        # --- Figure initialization
        figure_kws = {"figsize": getattr(p, "_figsize", None)}  # TODO fix
        self._figure = subplots.init_figure(
            pair_spec, self.pyplot, figure_kws, p._target,
        )

        # --- Figure annotation
        for sub in subplots:
            ax = sub["ax"]
            for axis in "xy":
                axis_key = sub[axis]
                # TODO Should we make it possible to use only one x/y label for
                # all rows/columns in a faceted plot? Maybe using sub{axis}label,
                # although the alignments of the labels from that method leaves
                # something to be desired (in terms of how it defines 'centered').
                names = [
                    common.names.get(axis_key),
                    *(layer["data"].names.get(axis_key) for layer in layers)
                ]
                label = next((name for name in names if name is not None), None)
                ax.set(**{f"{axis}label": label})

                # TODO there should be some override (in Plot.configure?) so that
                # tick labels can be shown on interior shared axes
                axis_obj = getattr(ax, f"{axis}axis")
                visible_side = {"x": "bottom", "y": "left"}.get(axis)
                show_axis_label = (
                    sub[visible_side]
                    or axis in p._pair_spec and bool(p._pair_spec.get("wrap"))
                    or not p._pair_spec.get("cartesian", True)
                )
                axis_obj.get_label().set_visible(show_axis_label)
                show_tick_labels = (
                    show_axis_label
                    or subplot_spec.get(f"share{axis}") not in (
                        True, "all", {"x": "col", "y": "row"}[axis]
                    )
                )
                for group in ("major", "minor"):
                    for t in getattr(axis_obj, f"get_{group}ticklabels")():
                        t.set_visible(show_tick_labels)

            # TODO title template should be configurable
            # ---- Also we want right-side titles for row facets in most cases?
            # ---- Or wrapped? That can get annoying too.
            # TODO should configure() accept a title= kwarg (for single subplot plots)?
            # Let's have what we currently call "margin titles" but properly using the
            # ax.set_title interface (see my gist)
            title_parts = []
            for dim in ["row", "col"]:
                if sub[dim] is not None:
                    name = common.names.get(dim)  # TODO None = val looks bad
                    title_parts.append(f"{name} = {sub[dim]}")

            has_col = sub["col"] is not None
            has_row = sub["row"] is not None
            show_title = (
                has_col and has_row
                or (has_col or has_row) and p._facet_spec.get("wrap")
                or (has_col and sub["top"])
                # TODO or has_row and sub["right"] and <right titles>
                or has_row  # TODO and not <right titles>
            )
            if title_parts:
                title = " | ".join(title_parts)
                title_text = ax.set_title(title)
                title_text.set_visible(show_title)

    def _transform_coords(self, p: Plot, common: PlotData, layers: list[Layer]) -> None:

        for var in p._variables:

            # Parse name to identify variable (x, y, xmin, etc.) and axis (x/y)
            # TODO should we have xmin0/xmin1 or x0min/x1min?
            m = re.match(r"^(?P<prefix>(?P<axis>[x|y])\d*).*", var)

            if m is None:
                continue

            prefix = m["prefix"]
            axis = m["axis"]

            share_state = self._subplots.subplot_spec[f"share{axis}"]

            # Concatenate layers, using only the relevant coordinate and faceting vars,
            # This is unnecessarily wasteful, as layer data will often be redundant.
            # But figuring out the minimal amount we need is more complicated.
            cols = [var, "col", "row"]
            # TODO basically copied from _setup_scales, and very clumsy
            layer_values = [common.frame.filter(cols)]
            for layer in layers:
                if layer["data"].frame is None:
                    for df in layer["data"].frames.values():
                        layer_values.append(df.filter(cols))
                else:
                    layer_values.append(layer["data"].frame.filter(cols))

            if layer_values:
                var_df = pd.concat(layer_values, ignore_index=True)
            else:
                var_df = pd.DataFrame(columns=cols)

            prop = Coordinate(axis)
            scale_spec = self._get_scale(p, prefix, prop, var_df[var])

            # Shared categorical axes are broken on matplotlib<3.4.0.
            # https://github.com/matplotlib/matplotlib/pull/18308
            # This only affects us when sharing *paired* axes. This is a novel/niche
            # behavior, so we will raise rather than hack together a workaround.
            if Version(mpl.__version__) < Version("3.4.0"):
                from seaborn._core.scales import Nominal
                paired_axis = axis in p._pair_spec
                cat_scale = isinstance(scale_spec, Nominal)
                ok_dim = {"x": "col", "y": "row"}[axis]
                shared_axes = share_state not in [False, "none", ok_dim]
                if paired_axis and cat_scale and shared_axes:
                    err = "Sharing paired categorical axes requires matplotlib>=3.4.0"
                    raise RuntimeError(err)

            # Now loop through each subplot, deriving the relevant seed data to setup
            # the scale (so that axis units / categories are initialized properly)
            # And then scale the data in each layer.
            subplots = [view for view in self._subplots if view[axis] == prefix]

            # Setup the scale on all of the data and plug it into self._scales
            # We do this because by the time we do self._setup_scales, coordinate data
            # will have been converted to floats already, so scale inference fails
            self._scales[var] = scale_spec.setup(var_df[var], prop)

            # Set up an empty series to receive the transformed values.
            # We need this to handle piecemeal tranforms of categories -> floats.
            transformed_data = []
            for layer in layers:
                index = layer["data"].frame.index
                transformed_data.append(pd.Series(dtype=float, index=index, name=var))

            for view in subplots:
                axis_obj = getattr(view["ax"], f"{axis}axis")

                if share_state in [True, "all"]:
                    # The all-shared case is easiest, every subplot sees all the data
                    seed_values = var_df[var]
                else:
                    # Otherwise, we need to setup separate scales for different subplots
                    if share_state in [False, "none"]:
                        # Fully independent axes are also easy: use each subplot's data
                        idx = self._get_subplot_index(var_df, view)
                    elif share_state in var_df:
                        # Sharing within row/col is more complicated
                        use_rows = var_df[share_state] == view[share_state]
                        idx = var_df.index[use_rows]
                    else:
                        # This configuration doesn't make much sense, but it's fine
                        idx = var_df.index

                    seed_values = var_df.loc[idx, var]

                scale = scale_spec.setup(seed_values, prop, axis=axis_obj)

                for layer, new_series in zip(layers, transformed_data):
                    layer_df = layer["data"].frame
                    if var in layer_df:
                        idx = self._get_subplot_index(layer_df, view)
                        new_series.loc[idx] = scale(layer_df.loc[idx, var])

                # TODO need decision about whether to do this or modify axis transform
                set_scale_obj(view["ax"], axis, scale.matplotlib_scale)

            # Now the transformed data series are complete, set update the layer data
            for layer, new_series in zip(layers, transformed_data):
                layer_df = layer["data"].frame
                if var in layer_df:
                    layer_df[var] = new_series

    def _compute_stats(self, spec: Plot, layers: list[Layer]) -> None:

        grouping_vars = [v for v in PROPERTIES if v not in "xy"]
        grouping_vars += ["col", "row", "group"]

        pair_vars = spec._pair_spec.get("structure", {})

        for layer in layers:

            data = layer["data"]
            mark = layer["mark"]
            stat = layer["stat"]

            if stat is None:
                continue

            iter_axes = itertools.product(*[
                pair_vars.get(axis, [axis]) for axis in "xy"
            ])

            old = data.frame

            if pair_vars:
                data.frames = {}
                data.frame = data.frame.iloc[:0]  # TODO to simplify typing

            for coord_vars in iter_axes:

                pairings = "xy", coord_vars

                df = old.copy()
                for axis, var in zip(*pairings):
                    if axis != var:
                        df = df.rename(columns={var: axis})
                        drop_cols = [x for x in df if re.match(rf"{axis}\d+", x)]
                        df = df.drop(drop_cols, axis=1)

                # TODO with the refactor we haven't set up scales at this point
                # But we need them to determine orient in ambiguous cases
                # It feels cumbersome to be doing this repeatedly, but I am not
                # sure if it is cleaner to make piecemeal additions to self._scales
                scales = {}
                for axis in "xy":
                    if axis in df:
                        prop = Coordinate(axis)
                        scale = self._get_scale(spec, axis, prop, df[axis])
                        scales[axis] = scale.setup(df[axis], prop)
                orient = layer["orient"] or mark._infer_orient(scales)

                if stat.group_by_orient:
                    grouper = [orient, *grouping_vars]
                else:
                    grouper = grouping_vars
                groupby = GroupBy(grouper)
                res = stat(df, groupby, orient, scales)

                if pair_vars:
                    data.frames[coord_vars] = res
                else:
                    data.frame = res

    def _get_scale(
        self, spec: Plot, var: str, prop: Property, values: Series
    ) -> ScaleSpec:

        if var in spec._scales:
            arg = spec._scales[var]
            if arg is None or isinstance(arg, ScaleSpec):
                scale = arg
            else:
                scale = prop.infer_scale(arg, values)
        else:
            scale = prop.default_scale(values)

        return scale

    def _setup_scales(self, p: Plot, layers: list[Layer]) -> None:

        # Identify all of the variables that will be used at some point in the plot
        variables = set()
        for layer in layers:
            if layer["data"].frame.empty and layer["data"].frames:
                for df in layer["data"].frames.values():
                    variables.update(df.columns)
            else:
                variables.update(layer["data"].frame.columns)

        for var in variables:

            if var in self._scales:
                # Scales for coordinate variables added in _transform_coords
                continue

            # Get the data all the distinct appearances of this variable.
            parts = []
            for layer in layers:
                if layer["data"].frame.empty and layer["data"].frames:
                    for df in layer["data"].frames.values():
                        parts.append(df.get(var))
                else:
                    parts.append(layer["data"].frame.get(var))
            var_values = pd.concat(
                parts, axis=0, join="inner", ignore_index=True
            ).rename(var)

            # Determine whether this is an coordinate variable
            # (i.e., x/y, paired x/y, or derivative such as xmax)
            m = re.match(r"^(?P<prefix>(?P<axis>x|y)\d*).*", var)
            if m is None:
                axis = None
            else:
                var = m["prefix"]
                axis = m["axis"]

            prop = PROPERTIES.get(var if axis is None else axis, Property())
            scale = self._get_scale(p, var, prop, var_values)

            # Initialize the data-dependent parameters of the scale
            # Note that this returns a copy and does not mutate the original
            # This dictionary is used by the semantic mappings
            if scale is None:
                # TODO what is the cleanest way to implement identity scale?
                # We don't really need a ScaleSpec, and Identity() will be
                # overloaded anyway (but maybe a general Identity object
                # that can be used as Scale/Mark/Stat/Move?)
                # Note that this may not be the right spacer to use
                # (but that is only relevant for coordinates where identity scale
                # doesn't make sense or is poorly defined — should it mean "pixes"?)
                self._scales[var] = Scale([], lambda x: x, None, "identity", None)
            else:
                self._scales[var] = scale.setup(var_values, prop)

    def _plot_layer(self, p: Plot, layer: Layer) -> None:

        data = layer["data"]
        mark = layer["mark"]
        move = layer["move"]

        default_grouping_vars = ["col", "row", "group"]  # TODO where best to define?
        grouping_properties = [v for v in PROPERTIES if v not in "xy"]

        pair_variables = p._pair_spec.get("structure", {})

        for subplots, df, scales in self._generate_pairings(data, pair_variables):

            orient = layer["orient"] or mark._infer_orient(scales)

            def get_order(var):
                # Ignore order for x/y: they have been scaled to numeric indices,
                # so any original order is no longer valid. Default ordering rules
                # sorted unique numbers will correctly reconstruct intended order
                # TODO This is tricky, make sure we add some tests for this
                if var not in "xy" and var in scales:
                    return scales[var].order

            if "width" in mark.features:
                width = mark._resolve(df, "width", None)
            elif "width" in df:
                width = df["width"]
            else:
                width = 0.8  # TODO what default?
            if orient in df:
                df["width"] = width * scales[orient].spacing(df[orient])

            if move is not None:
                moves = move if isinstance(move, list) else [move]
                for move in moves:
                    move_groupers = [
                        orient,
                        *(getattr(move, "by", None) or grouping_properties),
                        *default_grouping_vars,
                    ]
                    order = {var: get_order(var) for var in move_groupers}
                    groupby = GroupBy(order)
                    df = move(df, groupby, orient)

            # TODO unscale coords using axes transforms rather than scales?
            # Also need to handle derivatives (min/max/width, etc)
            df = self._unscale_coords(subplots, df, orient)

            grouping_vars = mark.grouping_vars + default_grouping_vars
            split_generator = self._setup_split_generator(
                grouping_vars, df, subplots
            )

            mark.plot(split_generator, scales, orient)

        # TODO is this the right place for this?
        for view in self._subplots:
            view["ax"].autoscale_view()

        self._update_legend_contents(mark, data, scales)

    def _scale_coords(self, subplots: list[dict], df: DataFrame) -> DataFrame:
        # TODO stricter type on subplots

        coord_cols = [c for c in df if re.match(r"^[xy]\D*$", c)]
        out_df = (
            df
            .copy(deep=False)
            .drop(coord_cols, axis=1)
            .reindex(df.columns, axis=1)  # So unscaled columns retain their place
        )

        for view in subplots:
            view_df = self._filter_subplot_data(df, view)
            axes_df = view_df[coord_cols]
            with pd.option_context("mode.use_inf_as_null", True):
                # TODO Is this just removing infs (since nans get added back?)
                axes_df = axes_df.dropna()
            for var, values in axes_df.items():
                scale = view[f"{var[0]}scale"]
                out_df.loc[values.index, var] = scale(values)

        return out_df

    def _unscale_coords(
        self, subplots: list[dict], df: DataFrame, orient: str,
    ) -> DataFrame:
        # TODO do we still have numbers in the variable name at this point?
        coord_cols = [c for c in df if re.match(r"^[xy]\D*$", c)]
        drop_cols = [*coord_cols, "width"] if "width" in df else coord_cols
        out_df = (
            df
            .drop(drop_cols, axis=1)
            .reindex(df.columns, axis=1)  # So unscaled columns retain their place
            .copy(deep=False)
        )

        for view in subplots:
            view_df = self._filter_subplot_data(df, view)
            axes_df = view_df[coord_cols]
            for var, values in axes_df.items():

                axis = getattr(view["ax"], f"{var[0]}axis")
                # TODO see https://github.com/matplotlib/matplotlib/issues/22713
                transform = axis.get_transform().inverted().transform
                inverted = transform(values)
                out_df.loc[values.index, var] = inverted

                if var == orient and "width" in view_df:
                    width = view_df["width"]
                    out_df.loc[values.index, "width"] = (
                        transform(values + width / 2) - transform(values - width / 2)
                    )

        return out_df

    def _generate_pairings(
        self, data: PlotData, pair_variables: dict,
    ) -> Generator[
        tuple[list[dict], DataFrame, dict[str, Scale]], None, None
    ]:
        # TODO retype return with subplot_spec or similar

        iter_axes = itertools.product(*[
            pair_variables.get(axis, [axis]) for axis in "xy"
        ])

        for x, y in iter_axes:

            subplots = []
            for view in self._subplots:
                if (view["x"] == x) and (view["y"] == y):
                    subplots.append(view)

            if data.frame.empty and data.frames:
                out_df = data.frames[(x, y)].copy()
            elif not pair_variables:
                out_df = data.frame.copy()
            else:
                if data.frame.empty and data.frames:
                    out_df = data.frames[(x, y)].copy()
                else:
                    out_df = data.frame.copy()

            scales = self._scales.copy()
            if x in out_df:
                scales["x"] = self._scales[x]
            if y in out_df:
                scales["y"] = self._scales[y]

            for axis, var in zip("xy", (x, y)):
                if axis != var:
                    out_df = out_df.rename(columns={var: axis})
                    cols = [col for col in out_df if re.match(rf"{axis}\d+", col)]
                    out_df = out_df.drop(cols, axis=1)

            yield subplots, out_df, scales

    def _get_subplot_index(self, df: DataFrame, subplot: dict) -> DataFrame:

        dims = df.columns.intersection(["col", "row"])
        if dims.empty:
            return df.index

        keep_rows = pd.Series(True, df.index, dtype=bool)
        for dim in dims:
            keep_rows &= df[dim] == subplot[dim]
        return df.index[keep_rows]

    def _filter_subplot_data(self, df: DataFrame, subplot: dict) -> DataFrame:
        # TODO being replaced by above function

        dims = df.columns.intersection(["col", "row"])
        if dims.empty:
            return df

        keep_rows = pd.Series(True, df.index, dtype=bool)
        for dim in dims:
            keep_rows &= df[dim] == subplot[dim]
        return df[keep_rows]

    def _setup_split_generator(
        self, grouping_vars: list[str], df: DataFrame, subplots: list[dict[str, Any]],
    ) -> Callable[[], Generator]:

        allow_empty = False  # TODO will need to recreate previous categorical plots

        grouping_keys = []
        grouping_vars = [
            v for v in grouping_vars if v in df and v not in ["col", "row"]
        ]
        for var in grouping_vars:
            order = self._scales[var].order
            if order is None:
                order = categorical_order(df[var])
            grouping_keys.append(order)

        def split_generator() -> Generator:

            for view in subplots:

                axes_df = self._filter_subplot_data(df, view)

                subplot_keys = {}
                for dim in ["col", "row"]:
                    if view[dim] is not None:
                        subplot_keys[dim] = view[dim]

                if not grouping_vars or not any(grouping_keys):
                    yield subplot_keys, axes_df.copy(), view["ax"]
                    continue

                grouped_df = axes_df.groupby(grouping_vars, sort=False, as_index=False)

                for key in itertools.product(*grouping_keys):

                    # Pandas fails with singleton tuple inputs
                    pd_key = key[0] if len(key) == 1 else key

                    try:
                        df_subset = grouped_df.get_group(pd_key)
                    except KeyError:
                        # TODO (from initial work on categorical plots refactor)
                        # We are adding this to allow backwards compatability
                        # with the empty artists that old categorical plots would
                        # add (before 0.12), which we may decide to break, in which
                        # case this option could be removed
                        df_subset = axes_df.loc[[]]

                    if df_subset.empty and not allow_empty:
                        continue

                    sub_vars = dict(zip(grouping_vars, key))
                    sub_vars.update(subplot_keys)

                    # TODO need copy(deep=...) policy (here, above, anywhere else?)
                    yield sub_vars, df_subset.copy(), view["ax"]

        return split_generator

    def _update_legend_contents(
        self, mark: Mark, data: PlotData, scales: dict[str, Scale]
    ) -> None:
        """Add legend artists / labels for one layer in the plot."""
        if data.frame.empty and data.frames:
            legend_vars = set()
            for frame in data.frames.values():
                legend_vars.update(frame.columns.intersection(scales))
        else:
            legend_vars = data.frame.columns.intersection(scales)

        # First pass: Identify the values that will be shown for each variable
        schema: list[tuple[
            tuple[str | None, str | int], list[str], tuple[list, list[str]]
        ]] = []
        schema = []
        for var in legend_vars:
            var_legend = scales[var].legend
            if var_legend is not None:
                values, labels = var_legend
                for (_, part_id), part_vars, _ in schema:
                    if data.ids[var] == part_id:
                        # Allow multiple plot semantics to represent same data variable
                        part_vars.append(var)
                        break
                else:
                    entry = (data.names[var], data.ids[var]), [var], (values, labels)
                    schema.append(entry)

        # Second pass, generate an artist corresponding to each value
        contents = []
        for key, variables, (values, labels) in schema:
            artists = []
            for val in values:
                artists.append(mark._legend_artist(variables, val, scales))
            contents.append((key, artists, labels))

        self._legend_contents.extend(contents)

    def _make_legend(self) -> None:
        """Create the legend artist(s) and add onto the figure."""
        # Combine artists representing same information across layers
        # Input list has an entry for each distinct variable in each layer
        # Output dict has an entry for each distinct variable
        merged_contents: dict[
            tuple[str | None, str | int], tuple[list[Artist], list[str]],
        ] = {}
        for key, artists, labels in self._legend_contents:
            # Key is (name, id); we need the id to resolve variable uniqueness,
            # but will need the name in the next step to title the legend
            if key in merged_contents:
                # Copy so inplace updates don't propagate back to legend_contents
                existing_artists = merged_contents[key][0]
                for i, artist in enumerate(existing_artists):
                    # Matplotlib accepts a tuple of artists and will overlay them
                    if isinstance(artist, tuple):
                        artist += artist[i],
                    else:
                        existing_artists[i] = artist, artists[i]
            else:
                merged_contents[key] = artists.copy(), labels

        base_legend = None
        for (name, _), (handles, labels) in merged_contents.items():

            legend = mpl.legend.Legend(
                self._figure,
                handles,
                labels,
                title=name,  # TODO don't show "None" as title
                loc="center left",
                bbox_to_anchor=(.98, .55),
            )

            # TODO: This is an illegal hack accessing private attributes on the legend
            # We need to sort out how we are going to handle this given that lack of a
            # proper API to do things like position legends relative to each other
            if base_legend:
                base_legend._legend_box._children.extend(legend._legend_box._children)
            else:
                base_legend = legend
                self._figure.legends.append(legend)
