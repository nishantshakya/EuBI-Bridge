import sys
import tifffile
import copy, glob, re, os, itertools
from pathlib import Path
import dask.array as da, numpy as np, dask
from collections import Counter
from typing import Iterable, Callable, Union, List, Tuple
from natsort import natsorted

from eubi_bridge.ngff import defaults

transpose_list = lambda l: list(map(list, zip(*l)))
get_numerics = lambda string: list(re.findall(r'\d+', string))
get_alpha = lambda string: ''.join([i for i in string if not i.isnumeric()])


def get_matches(pattern, strings, return_non_matches=False):
    matches = [re.search(pattern, string) for string in strings]
    if return_non_matches:
        return matches
    return [match for match in matches if match is not None]


def split_by_match(filepaths, *args):  # Group per channel using channel query
    ret = dict().fromkeys(args)
    for key in args:
        matches = get_matches(key, filepaths)
        ret[key] = matches
    return ret


def find_match_and_numeric(filepaths, *args):
    ret = {}
    for key in args:
        matches = get_matches(key, filepaths)
        for match in matches:
            span = match.string[match.start():match.end()]
            if span not in ret.keys():
                ret[span] = [match]
            else:
                ret[span].append(match)
    return ret


def concatenate_shapes_along_axis(shapes: Iterable,
                                  axis: int
                                  ):
    reference_shape = shapes[0]
    concatenated_shape = [num for num in reference_shape]
    for shape in shapes[1:]:
        for idx, size in enumerate(shape):
            if idx == axis:
                concatenated_shape[idx] += size
            else:
                assert size == reference_shape[idx], ValueError(
                    "For concatenation to succeed, all dimensions except the dimension of concatenation must match.")
    return concatenated_shape


def accumulate_slices_along_axis(shapes: Iterable,
                                 axis: int,
                                 slices: Union[tuple, list] = None
                                 ):
    reference_shape = shapes[0]
    if slices is None:
        slices = [[slice(None, None) for _ in reference_shape] for _ in shapes]
    assert (len(shapes) == len(slices))
    sizes_per_axis = [shape[axis] for shape in shapes]
    cummulative_sizes = [0] + np.cumsum(sizes_per_axis).tolist()
    slice_tuples = [(cummulative_sizes[idx], cummulative_sizes[idx + 1]) for idx in range(len(sizes_per_axis))]
    for idx, tup in enumerate(slice_tuples):
        slc = slices[idx]
        slclist = list(slc)
        slclist[axis] = slice(*tup)
        slices[idx] = tuple(slclist)
    return slices


def _reduce_paths(paths: Iterable[str],
                  dimension_tag,
                  reduced_paths: Iterable[str] = None,
                  replacement='set',
                  ):
    # replacement = dimension_tag.replace(',', '') + f'{replace_with}'
    # print(dimension_tag, replacement)
    if reduced_paths is None:
        reduced_paths = copy.deepcopy(paths)
    matches = get_matches(f'{dimension_tag}\d+', paths, return_non_matches=True)
    if all(item is None for item in matches):
        matches = get_matches(dimension_tag, paths, return_non_matches=True)
    for idx, match in enumerate(matches):
        if match is None:
            pass
        else:
            span = match.string[match.start():match.end()]
            string = match.string.replace(span, replacement)
            reduced_paths[idx] = string
    return reduced_paths


def _reduce_paths_with_tuple(paths: Iterable[str],
                             dimension_tag: Union[tuple, list],
                             replacement='set'
                             ):
    assert isinstance(dimension_tag, (tuple, list))
    combined_pattern = "".join(dimension_tag) + replacement
    pattern_regex = r'(' + '|'.join(map(re.escape, dimension_tag)) + r')'
    reduced_paths = []
    for path in paths:
        if re.search(pattern_regex, path):
            reduced_paths.append(re.sub(pattern_regex, combined_pattern, path))
        else:
            reduced_paths.append(path)
    return reduced_paths


def reduce_paths(paths: Iterable[str],
                 dimension_tag: Union[tuple, str],
                 reduced_paths: Iterable[str] = None,
                 replace_with: str = 'set'
                 ) -> List[str]:
    if isinstance(dimension_tag, str):
        replacement = dimension_tag.replace(',', '') + f'{replace_with}'
        return _reduce_paths(paths, dimension_tag, reduced_paths, replacement)
    elif isinstance(dimension_tag, (tuple, list)):
        return _reduce_paths_with_tuple(paths, dimension_tag, replace_with)


# def array_concatenate(arrays, axis=0):
#     """Efficiently concatenate Dask arrays using map_blocks."""
#     arrays = [da.asarray(arr) for arr in arrays]  # Ensure inputs are Dask arrays
#     return da.map_blocks(
#         da.concatenate, arrays, axis=axis, dtype=arrays[0].dtype
#     )


class FileSet:  # TODO: add a pixel_size parameter
    """
    Make sure the filepaths are sorted before passing them to this class.
    This class also assumes that the input files can contain maximum 5 dimensions.
    """

    def __init__(self,
                 filepaths: Iterable[str],
                 shapes: Iterable[tuple | list] = None,
                 axis_tag0: Union[str, tuple] = None,
                 axis_tag1: Union[str, tuple] = None,
                 axis_tag2: Union[str, tuple] = None,
                 axis_tag3: Union[str, tuple] = None,
                 axis_tag4: Union[str, tuple] = None,
                 arrays: Iterable[da.Array] = None
                 # pixel_sizes: Iterable = None # TODO
                 ):
        assert shapes is not None or arrays is not None, f"Either shapes or arrays must be supplied."

        if arrays is not None:
            self.array_dict = dict(zip(filepaths, arrays))
            shapes = [arr.shape for arr in arrays]
        else:
            self.array_dict = None

        self.region_dict = {path: arr.copy() for path, arr in self.array_dict.items()}

        self.shape_dict = dict(zip(filepaths, shapes))
        # self.axis_tag0 = axis_tag0
        # self.axis_tag1 = axis_tag1
        # self.axis_tag2 = axis_tag2
        # self.axis_tag3 = axis_tag3
        # self.axis_tag4 = axis_tag4
        # self.axis_tags = {
        #     0: axis_tag0,
        #     1: axis_tag1,
        #     2: axis_tag2,
        #     3: axis_tag3,
        #     4: axis_tag4
        # }
        full_axis_list = list(range(5))
        self.axis_tags = [axis_tag0, axis_tag1, axis_tag2, axis_tag3, axis_tag4]
        dimension_tags, specified_axes = [], []
        for axis, tag in zip(full_axis_list, self.axis_tags):
            if tag is not None:
                dimension_tags.append(tag)
                specified_axes.append(axis)

        self.dimension_tags = dimension_tags
        self.specified_axes = specified_axes

        self.group = {'': filepaths}
        assert len(self.dimension_tags) == len(self.specified_axes)
        self.slice_dict = {path: tuple(slice(0, size) for size in shape) for path, shape in self.shape_dict.items()}
        self.path_dict = dict(zip(filepaths, filepaths))
        # self._reference_filepath = filepaths[0]
        # self.detect_voxel_meta()

    # def detect_voxel_meta(self):
    #     self.vmeta = VoxelMetaReader(self._reference_filepath)
    # def _axis_as_str(self, axis: int):
    #     ax_dict = {0 : 't',
    #                1: 'c',
    #                2: 'z',
    #                3: 'y',
    #                4: 'x'
    #                }
    #     return ax_dict[axis]

    # def _get_concatenation_axis(self, dimension_tag):
    #     return self.specified_axes[self.dimension_tags.index(dimension_tag)]

    def get_numerics_per_dimension_tag(self,
                                       dimension_tag
                                       ):
        filepaths = list(self.group.values())[0]
        matches = get_matches(f'{dimension_tag}\d+', filepaths)
        spans = [match.string[match.start():match.end()] for match in matches]
        numerics = [get_numerics(span)[0] for span in spans]
        # TODO: add an incrementality validator
        return numerics

    def _csplit_by(self, tup: tuple):  # TODO: maybe redefine as nonserial_split?
        group = copy.deepcopy(self.group)
        for key, filepaths in group.items():
            alpha_dict = {key: [] for key in tup}
            for tag in tup:
                matches = get_matches(f'{tag}', filepaths)
                spans = [match.string[match.start():match.end()] for match in matches]
                matched_paths = [match.string for match in matches]
                alpha = copy.deepcopy(spans)
                alpha_categories = np.unique(alpha).tolist()
                assert len(alpha_categories) == 1, f"Number of categories is not 1: {alpha_categories}"
                alpha_tag = alpha_categories[0]
                alpha_dict[alpha_tag] = matched_paths
            group = alpha_dict
        return group

    def _split_by(self, *args):
        group = copy.deepcopy(self.group)
        for dim in args:
            if dim not in self.dimension_tags:
                raise ValueError(f"The dimension '{dim}' is not among the given dimension_tags.")
            if isinstance(dim, (tuple, list)):
                group = self._csplit_by(dim)
            else:
                numeric_dict = {}
                for key, filepaths in group.items():
                    matches = get_matches(f'{dim}\d+', filepaths)
                    spans = [match.string[match.start():match.end()] for match in matches]
                    spans = [span.replace(dim, '') for span in spans]  ### remove search term from the spans
                    numerics = [get_numerics(span)[0] for span in spans]
                    numeric_categories = np.unique(numerics).tolist()
                    for idx, num in enumerate(numerics):
                        for i, category in enumerate(numeric_categories):
                            if num == category:
                                if key != '':
                                    tag_key = ''.join([key, '-', dim, num])
                                else:
                                    tag_key = ''.join([dim, num])
                                    # print(f"hey: {dim, num, tag_key}")
                                if not tag_key in numeric_dict:
                                    numeric_dict[tag_key] = []
                                numeric_dict[tag_key].append(filepaths[idx])
                group = numeric_dict
        return group

    def concatenate_along(self,
                          axis: int
                          ):
        ax_dict = {0: 't',
                   1: 'c',
                   2: 'z',
                   3: 'y',
                   4: 'x'
                   }
        # dimension_tag = self.__getattribute__(f'axis_tag{axis}')
        dimension_tag = self.axis_tags[axis]
        if not dimension_tag in self.dimension_tags:
            raise ValueError(f"The dimension '{dimension_tag}' is not among the given dimension_tags.")
        to_split = [item for item in self.dimension_tags if item != dimension_tag]
        group = self._split_by(*to_split)
        axis = self.specified_axes[self.dimension_tags.index(dimension_tag)]
        for key, paths in group.items():
            sorted_paths = natsorted(paths)
            group_slices = [self.slice_dict[path] for path in sorted_paths]
            group_shapes = [self.shape_dict[path] for path in sorted_paths]
            group_reduced_paths = [self.path_dict[path] for path in sorted_paths]

            new_slices = accumulate_slices_along_axis(group_shapes, axis, group_slices)
            new_shape = concatenate_shapes_along_axis(group_shapes, axis)
            # ax_str = self._axis_as_str(axis)
            new_reduced_paths = reduce_paths(group_reduced_paths, dimension_tag,
                                             group_reduced_paths,
                                             f'_{ax_dict[axis]}set')

            if self.array_dict is not None:
                group_arrays = [self.array_dict[path] for path in sorted_paths]
                new_array = da.concatenate(group_arrays, axis=axis)

            for path, slc, reduced_path in zip(sorted_paths, new_slices, new_reduced_paths):
                self.slice_dict[path] = slc
                self.shape_dict[path] = new_shape
                self.path_dict[path] = reduced_path
                if self.array_dict is not None:
                    self.array_dict[path] = new_array

        return group

    # def get_table(self):
    #     import pandas as pd
    #     df = pd.DataFrame({"path": self.path_dict,
    #                        "slice": self.slice_dict,
    #                        "shape": self.shape_dict})
    #     return df

    # @property
    # def table(self):
    #     try:
    #         return self.get_table()
    #     except:
    #         return None

    # def refine_path_dict(self,
    #                      root_dir = None,
    #                      exception = 'set'
    #                      ):
    #     path_dict = copy.deepcopy(self.path_dict)
    #     # path_dict = copy.deepcopy(fsio.path_dict)
    #     paths = list(path_dict.values())
    #     # unique_path_num = np.unique(paths).size
    #     def split_path(path):
    #         s = path.split('/')
    #         if s[0] == '': s = s[1:]
    #         return s
    #     splitted = list(map(split_path, paths))
    #     shortest_item = min(splitted, key = len)
    #     length_shortest = len(shortest_item)
    #
    #     tails = []
    #     for i in range(1, length_shortest):
    #         r = length_shortest - i
    #         last_tails = copy.deepcopy(tails)
    #         roots, tails = [], []
    #         for item in splitted:
    #             tail = '-'.join(item[::-1][:r][::-1])
    #             root = '-'.join(item[::-1][r:][::-1])
    #             tail, _ = os.path.splitext(tail)
    #             roots.append(root)
    #             tails.append(tail)
    #
    #         unique_roots = np.unique(roots).tolist()
    #         unique_root_num = np.unique(roots).size
    #         if unique_root_num == 1:
    #             unique_root = unique_roots[0]
    #         if (unique_root_num > 1):
    #             valid_tails = last_tails
    #             break
    #         elif unique_root_num == 1:
    #             if exception in unique_root:
    #                 valid_tails = last_tails
    #                 break
    #             else:
    #                 valid_tails = tails
    #         else:
    #             raise Exception(f"Something seriously wrong happened.")
    #
    #     for key, tail in zip(list(path_dict.keys()), valid_tails):
    #         if root_dir is None:
    #             self.path_dict[key] = tail
    #         else:
    #             self.path_dict[key] = os.path.join(root_dir, tail)
    #     return tails

    def get_concatenated_arrays(self):
        unique_ids = []
        unique_paths = []
        for key, path in self.path_dict.items():
            if path not in unique_paths:
                unique_paths.append(path)
                unique_ids.append(key)
        unique_arrays = [self.array_dict[path] for path in unique_ids]
        return dict(zip(unique_paths, unique_arrays))
