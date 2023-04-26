"""
This module contains the logic for regridding from a structured form to another
structured form. All coordinates are assumed to be fully orthogonal to each
other.

While the unstructured logic would work for structured data as well, it is much
less efficient than utilizing the structure of the coordinates.
"""
from typing import Union

import numpy as np
import xarray as xr

from xugrid.regrid.overlap_1d import overlap_1d, overlap_1d_nd
from xugrid.regrid.utils import broadcast


class StructuredGrid1d:
    """
    e.g. z -> z; so also works for unstructured

    Parameters
    ----------
    bounds: (n, 2)
    """

    def __init__(self, obj: Union[xr.DataArray, xr.Dataset], name: str):
        bounds_name = f"{name}bounds"  # e.g. xbounds
        size_name = f"d{name}"  # e.g. dx

        index = obj.indexes[name]
        # take care of potentially decreasing coordinate values
        if index.is_monotonic_decreasing:
            midpoints = index.values[::-1]
            flipped = True
        elif index.is_monotonic_increasing:
            midpoints = index.values
            flipped = False
        else:
            raise ValueError(f"{name} is not monotonic for array {obj.name}")

        if bounds_name in obj.coords:
            bounds = obj[bounds_name].values
        else:
            if size_name in obj.coords:
                # works for scalar size and array size
                size = np.abs(obj[size_name].values)
            else:
                # no bounds defined, no dx defined
                # make an estimate of cell size
                size = np.diff(midpoints)
                # Check if equidistant
                atolx = 1.0e-4 * size[0]
                if not np.allclose(size, size[0], atolx):
                    raise ValueError(
                        f"DataArray has to be equidistant along {name}, or "
                        f'explicit bounds must be given as "{name}bounds", or '
                        f'cellsizes must be as "d{name}"'
                    )

            start = midpoints - 0.5 * size
            end = midpoints + 0.5 * size
            bounds = np.column_stack((start, end))

        self.name = name
        self.midpoints = midpoints
        self.bounds = bounds
        self.flipped = flipped
        self.grid = obj

    @property
    def ndim(self):
        return 1

    @property
    def dims(self):
        return (self.name,)

    @property
    def size(self):
        return len(self.bounds)

    @property
    def length(self):
        return abs(np.diff(self.bounds, axis=1))

    def flip_if_needed(self, index):
        if self.flipped:
            return self.size - index - 1
        else:
            return index

    def valid_nodes_index(self, other):
        """retruns all nodes that are within the bounding box of overlaying grid
        Args:
            other (StructuredGrid1d): overlaying grid from wich bounding boxes are checked

        Returns:
            valid_self_index (np.array): valid self indexes
            valid_other_index (np.array): corresponding other indexes
        """
        # left aligned to nodes, not to coordinates
        side = "left"
        if self.flipped:
            side = "right"
        start = np.searchsorted(other.bounds[:, 0], self.midpoints, side=side)
        end = np.searchsorted(other.bounds[:, 1], self.midpoints, side=side)
        valid = (
            (start == (end + 1))
            & (self.midpoints > other.bounds[0, 0])
            & (self.midpoints < other.bounds[-1, 1])
        )
        valid_other_index = end[valid]
        valid_self_index = np.arange(self.size)[valid]
        return valid_self_index, valid_other_index

    def overlap(self, other: "StructuredGrid1d", relative: bool):
        """returns nodes and length of overlaying other grid
        This function has no checks for validity of nodes by bounding boxes at this point
        Args:
            other (StructuredGrid1d): overlaying grid
            relative (bool): True: lenght of overlap, False: lenght of overlap as fraction

        Returns:
            source_index (np.array): overlaying self indexes
            target_index (np.array): overlaying other indexes
            weights (np.array): lenght or fraction of overlap
        """
        source_index, target_index, weights = overlap_1d(self.bounds, other.bounds)
        source_index = self.flip_if_needed(source_index)
        target_index = other.flip_if_needed(target_index)
        if relative:
            weights /= self.length()[source_index]
        return source_index, target_index, weights

    def locate_centroids(self, other: "StructuredGrid1d"):
        """returns valid nodes and there overlapping other grid id's

        Args:
            other (StructuredGrid1d): overlaying grid

        Returns:
            source_index (np.array): overlaying self indexes
            target_index (np.array): overlaying other indexes
            weights (np.array): array of ones
        """
        source_index, target_index = self.valid_nodes_index(other)
        source_index = self.flip_if_needed(source_index)
        target_index = other.flip_if_needed(target_index)
        weights = np.ones(source_index.size, dtype=float)
        return source_index, target_index, weights

    def linear_weights(self, other: "StructuredGrid1d"):
        """returns valid nodes and there linear weights

        Args:
            other (StructuredGrid1d): overlaying grid

        Raises:
            ValueError: when number of nodes is to small to compute linear weights

        Returns:
            source_index (np.array): overlaying self indexes
            target_index (np.array): overlaying other indexes
            weights (np.array): array linear weights
        """
        source_index_midpoints = self.midpoints
        target_index_midpoints = other.midpoints
        if not source_index_midpoints.size > 2:
            raise ValueError(
                "source index must larger than 2. Cannot interpolate with one point"
            )
        source_index, target_index = self.valid_nodes_index(other)
        source_index = self.flip_if_needed(source_index)
        target_index = other.flip_if_needed(target_index)
        isource = source_index - 1
        weights = (
            target_index_midpoints[target_index] - source_index_midpoints[isource]
        ) / (source_index_midpoints[isource + 1] - source_index_midpoints[isource])
        weights[weights < 0.0] = 0.0
        weights[weights > 1.0] = 1.0
        source_index = np.repeat(source_index, 2)
        target_index = np.column_stack((target_index, target_index + 1)).ravel()
        weights = np.column_stack((weights, 1.0 - weights)).ravel()
        return source_index, target_index, weights


class StructuredGrid2d(StructuredGrid1d):
    """
    e.g. (x,y) -> (x,y)
    """

    def __init__(
        self,
        obj: Union[xr.DataArray, xr.Dataset],
        name_x: str,
        name_y: str,
    ):
        self.xbounds = StructuredGrid1d(obj, name_x)
        self.ybounds = StructuredGrid1d(obj, name_y)

    @property
    def ndim(self):
        return 2

    @property
    def dims(self):
        return self.ybounds.dims + self.xbounds.dims  # ("y", "x")

    @property
    def size(self):
        return self.ybounds.size * self.xbounds.size

    @property
    def shape(self):
        return (self.ybounds.size, self.xbounds.size)

    @property
    def area(self):
        return np.multiply.outer(self.ybounds.length, self.xbounds.length)
    
    def convert_to(self, matched_type):
        if isinstance(self, matched_type):
            return self
        elif isinstance(UnstructuredGrid2d):
            return Ugrid2d.from_structured(self.xbounds, self.ybounds)
        else:
            raise TypeError(f"Cannot convert StructuredGrid2d to {matched_type.__name__}")

    def overlap(self, other, relative: bool):
        """
        Returns
        -------
        source_index: 1d np.ndarray of int
        target_index: 1d np.ndarray of int
        weights: 1d np.ndarray of float
        """
        source_index_x, target_index_x, weights_x = self.xbounds.overlap(
            other.xbounds, relative
        )
        source_index_y, target_index_y, weights_y = self.ybounds.overlap(
            other.ybounds, relative
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_y, source_index_x),
            (target_index_y, target_index_x),
            (weights_y, weights_x),
        )

    def locate_centroids(self, other):
        source_index_x, target_index_x, weights_x = self.xbounds.locate_centroids(
            other.xbounds
        )
        source_index_y, target_index_y, weights_y = self.ybounds.locate_centroids(
            other.ybounds
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_y, source_index_x),
            (target_index_y, target_index_x),
            (weights_y, weights_x),
        )

    def linear_weights(self, other):
        source_index_x, target_index_x, weights_x = self.xbounds.linear_weights(
            other.xbounds
        )
        source_index_y, target_index_y, weights_y = self.ybounds.linear_weights(
            other.ybounds
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_y, source_index_x),
            (target_index_y, target_index_x),
            (weights_y, weights_x),
        )


class StructuredGrid3d:
    """
    e.g. (x,y,z) -> (x,y,z)

    A voxel model (GeoTOP)
    """

    def __init__(
        self,
        obj: Union[xr.DataArray, xr.Dataset],
        name_x: str,
        name_y: str,
        name_z: str,
    ):
        self.xbounds = StructuredGrid1d(obj, name_x)
        self.ybounds = StructuredGrid1d(obj, name_y)
        self.zbounds = StructuredGrid1d(obj, name_z)

    @property
    def shape(self):
        return (self.zbound.size, self.ybounds.size, self.xbounds.size)

    @property
    def volume(self):
        return np.multiply.outer(self.zbounds.length, self.area)

    def overlap(self, other, relative: bool):
        """
        Returns
        -------
        source_index: 1d np.ndarray of int
        target_index: 1d np.ndarray of int
        weights: 1d np.ndarray of float
        """
        source_index_x, target_index_x, weights_x = self.xbounds.overlap(
            other.xbounds, relative
        )
        source_index_y, target_index_y, weights_y = self.ybounds.overlap(
            other.ybounds, relative
        )
        source_index_z, target_index_z, weights_z = self.zbounds.overlap(
            other.zbounds, relative
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_z, source_index_y, source_index_x),
            (target_index_z, target_index_y, target_index_x),
            (weights_z, weights_y, weights_x),
        )

    def locate_centroids(self, other):
        source_index_x, target_index_x, weights_x = self.xbounds.locate_centroids(
            other.xbounds
        )
        source_index_y, target_index_y, weights_y = self.ybounds.locate_centroids(
            other.ybounds
        )
        source_index_z, target_index_z, weights_z = self.zbounds.locate_centroids(
            other.zbounds
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_z, source_index_y, source_index_x),
            (target_index_z, target_index_y, target_index_x),
            (weights_z, weights_y, weights_x),
        )

    def linear_weights(self, other):
        source_index_x, target_index_x, weights_x = self.xbounds.linear_weights(
            other.xbounds
        )
        source_index_y, target_index_y, weights_y = self.ybounds.linear_weights(
            other.ybounds
        )
        source_index_z, target_index_z, weights_z = self.zbounds.linear_weights(
            other.zbounds
        )
        return broadcast(
            self.shape,
            other.shape,
            (source_index_z, source_index_y, source_index_x),
            (target_index_z, target_index_y, target_index_x),
            (weights_z, weights_y, weights_x),
        )


class ExplicitStructuredGrid3d:
    """
    e.g. (x,y,z) -> (x,y,z)

    A layered model (E.g. REGIS)
    """

    def __init__(
        self,
        obj: Union[xr.DataArray, xr.Dataset],
    ):
        # zbounds is a 3D array with dimensions (nlayer, y.size * x.size, 2)
        self.xbounds = StructuredGrid1d(obj, "x")
        self.ybounds = StructuredGrid1d(obj, "y")
        self.zbounds = StructuredGrid1d(obj, "z")

    @property
    def shape(self):
        raise NotImplementedError

    @property
    def volume(self):
        return np.multiply.outer(self.zbounds.length, self.area)

    def overlap(self, other, relative: bool):
        """
        Returns
        -------
        source_index: 1d np.ndarray of int
        target_index: 1d np.ndarray of int
        weights: 1d np.ndarray of float
        """
        source_index_x, target_index_x, weights_x = self.xbounds.overlap(
            other.xbounds, relative
        )
        source_index_y, target_index_y, weights_y = self.ybounds.overlap(
            other.ybounds, relative
        )
        source_index_yx, target_index_yx, weights_yx = broadcast(
            self.shape,
            other.shape,
            (source_index_y, source_index_x),
            (target_index_y, target_index_x),
            (weights_y, weights_x),
        )

        if isinstance(other, StructuredGrid3d):
            zbounds = other.zbounds[np.newaxis, ...]
            target_index = np.zeros(zbounds.shape[1], dtype=int)
        elif isinstance(other, ExplicitStructuredGrid3d):
            zbounds = other.zbounds
            target_index = target_index_yx
        else:
            raise TypeError

        source_index_zyx, target_index_zyx, weights_z = overlap_1d_nd(
            self.zbounds,
            zbounds,
            source_index_yx,
            target_index,
        )
        # TODO: check array dims
        weights_zyx = weights_z * weights_yx
        return source_index_zyx, target_index_zyx, weights_zyx
