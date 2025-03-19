import bpy
import numpy as np
import bmesh
import random
from enum import Enum
from skimage.measure import marching_cubes
from typing import Tuple, List, Dict, Set, Any
from scipy.spatial import KDTree
from scipy.ndimage import distance_transform_edt

from .poisson_disk_sampling import poisson_disk_sampling_on_surface

class CellType(Enum):
  no_tree = 0
  stem = 1
  crown = 2
  collision = 3

class VoxelGrid:
  """
  The class used to manage trees in a simulated forest.
  Each tree is represented by a voxel grid where each cell is either filled (1) or empty (0).
  The voxel grid is centered at the stem of the tree.
  The class provides a method for generating a mesh from the tree voxel grids.
  A mesh is generated by lazily evaluating the forest and resolving collisions between trees.
  """
  
  def __init__(self):
    self.evaluated_forest = False
    
    # The first three elements of this tuple are the position of the tree, with the position of the tree being the position of the stem.
    # This position is in the middle of the grid (4th element). This does not need to be true when the crown is asymmetrical.
    self.trees: List[Tuple[int, int, int, int, np.ndarray]] = []

    self.cube_size = 0.5

  def generate_mesh(self, index):  
    crown_mesh = self.generate_crown_mesh(index)
    
    return self.trees[index][3], crown_mesh
    
  def generate_crown_mesh(self, index):
    mesh = bpy.data.meshes.new("CrownMesh")
    obj = bpy.data.objects.new(f"Crown_Voxel_{index}", mesh)

    bm = bmesh.new()
    
    tree_grid = self.trees[index][-1]

    for x in range(len(tree_grid)):
        for y in range(len(tree_grid[x])):
            for z in range(len(tree_grid[x][y])):
                if tree_grid[x][y][z] == CellType.crown.value:
                    self.add_voxel_to_bmesh(bm, x, y, z, tree_grid, self.cube_size)

    bm.to_mesh(mesh)
    bm.free()
    obj.location = tuple(np.array(self.trees[index][:3]) * self.cube_size)
    return obj
  
  def add_voxel_to_bmesh(self, bm, x, y, z, tree_grid, size):
    voxel_pos = (x * size, y * size, z * size)
    
    offsets = [
      (-1, 0, 0), 
      (1, 0, 0), 
      (0, -1, 0), 
      (0, 1, 0), 
      (0, 0, -1), 
      (0, 0, 1)
    ]
    
    neighbors_filled = self.get_neighbors_filled(x, y, z, offsets, tree_grid)
    index_to_face_direction = ["left", "right", "front", "back", "bottom", "top"]
    for index, neighbor in enumerate(neighbors_filled):
      if not neighbor:
        self.add_face_to_bmesh(bm, voxel_pos, size, index_to_face_direction[index])

  def add_face_to_bmesh(self, bm, position, size, face):
    x, y, z = position
    hs = size / 2.0  # Half-size of the cube
    
    # Define cube vertices
    if face == "left":
        verts = [(x - hs, y - hs, z - hs), (x - hs, y - hs, z + hs), (x - hs, y + hs, z + hs), (x - hs, y + hs, z - hs)]
    elif face == "right":
        verts = [(x + hs, y - hs, z - hs), (x + hs, y - hs, z + hs), (x + hs, y + hs, z + hs), (x + hs, y + hs, z - hs)]
    elif face == "front":
        verts = [(x - hs, y - hs, z - hs), (x + hs, y - hs, z - hs), (x + hs, y - hs, z + hs), (x - hs, y - hs, z + hs)]
    elif face == "back":
        verts = [(x - hs, y + hs, z - hs), (x + hs, y + hs, z - hs), (x + hs, y + hs, z + hs), (x - hs, y + hs, z + hs)]
    elif face == "bottom":
        verts = [(x - hs, y - hs, z - hs), (x + hs, y - hs, z - hs), (x + hs, y + hs, z - hs), (x - hs, y + hs, z - hs)]
    elif face == "top":
        verts = [(x - hs, y - hs, z + hs), (x + hs, y - hs, z + hs), (x + hs, y + hs, z + hs), (x - hs, y + hs, z + hs)]

    bm_verts = [bm.verts.new(v) for v in verts]
    bm.faces.new(bm_verts)
  
  def get_neighbors_filled(self, x, y, z, offsets, tree_grid):
    filled_neighbors = []
    for offset in offsets:
      filled_neighbors.append(self.is_filled(x + offset[0], y + offset[1], z + offset[2], tree_grid))
    return filled_neighbors
      
  def is_filled(self, x, y, z, tree_grid):
    if not (0 <= x < len(tree_grid) and 0 <= y < len(tree_grid[x]) and 0 <= z < len(tree_grid[x][y])):
      return False 
    return tree_grid[x][y][z] == CellType.crown.value
      
  def generate_forest(self, tree_configurations: List[Dict[str, Any]], configuration_weights: List[float], surface: List[Tuple[int, int]]):
    crown_widths = [tree_configuration["crown_width"] for tree_configuration in tree_configurations]
    sampled_points = poisson_disk_sampling_on_surface(surface, configuration_weights, crown_widths)
    for sampled_point in sampled_points:
      sampled_position = sampled_point[0]
      chosen_configuration_index = sampled_point[1]
      self.add_tree((sampled_position[0], sampled_position[1], 0), chosen_configuration_index, tree_configurations[chosen_configuration_index])
    self.evaluate_forest(tree_configurations)
    self.evaluated_forest = True 
    
  def add_tree(self, position: Tuple[int, int, int], configuration_identifier: int, tree_configuration: dict[str, float]):
    crown_type_to_function = {
      "ellipsoid": self.add_ellipsoid_tree,
      "columnar": self.add_columnar_tree,
      "spreading": self.add_spreading_tree
    }
    
    self.evaluated_forest = False
    stem_height = tree_configuration["stem_height"]
    stem_diameter = tree_configuration["stem_diameter"]
    crown_width = tree_configuration["crown_width"]
    crown_height = tree_configuration["crown_height"]
    crown_offset = tree_configuration["crown_offset"]
    
    tree_grid = np.zeros((
      int(crown_width / self.cube_size + 1) + 1, 
      int(crown_width / self.cube_size + 1) + 1, 
      int((stem_height + crown_height - crown_offset) / self.cube_size + 1) + 1), 
      dtype=np.int8
    )
    
    self.add_stem(tree_grid, stem_diameter, stem_height)
    crown_type_to_function[tree_configuration["crown_type"]](tree_grid, tree_configuration)
    self.trees.append((int(position[0] / self.cube_size), int(position[1] / self.cube_size), int(position[2] / self.cube_size), configuration_identifier, tree_grid))
    
  def add_stem(self, tree_grid: np.ndarray, stem_diameter: float, stem_height: float):
    stem_radius = int(stem_diameter / 2 / self.cube_size)

    #Add stem
    stem_height_range = np.arange(int(stem_height / self.cube_size))
    stem_diameter_range = np.arange(-int(stem_diameter / self.cube_size), int(stem_diameter / self.cube_size))
    j, k = np.meshgrid(stem_diameter_range, stem_diameter_range, indexing='ij')
    mask = j**2 + k**2 <= stem_radius**2

    for i in stem_height_range:
      tree_grid[j[mask]+tree_grid.shape[0]//2, k[mask]+tree_grid.shape[1]//2, i] = CellType.stem.value
  
  def add_ellipsoid_tree(self, tree_grid: np.ndarray, tree_configuration: dict[str, float]):
    """
    Adds a tree to the voxel grid at the specified position with the given dimensions.
    
    :param position: The (x, y, z) coordinates where the tree will be added.
    :type position: Tuple[int, int, int]
    :param stem_diameter: The diameter of the tree's stem.
    :type stem_diameter: float
    :param stem_height: The height of the tree's stem.
    :type stem_height: float
    :param crown_diameter: The diameter of the tree's crown.
    :type crown_diameter: float
    :return: None
    :rtype: None
    """
    
    stem_height = tree_configuration["stem_height"]
    crown_width = tree_configuration["crown_width"]
    crown_height = tree_configuration["crown_height"]
    crown_offset = tree_configuration["crown_offset"]
    
    half_width_cube_size = int(crown_width / 2 / self.cube_size)
    half_height_cube_size = int(crown_height / 2 / self.cube_size)
    
    crown_range_xy = np.arange(-half_width_cube_size, half_width_cube_size + 1)
    crown_range_z = np.arange(-half_height_cube_size, half_height_cube_size + 1)
    i, j, k = np.meshgrid(crown_range_xy, crown_range_xy, crown_range_z, indexing='ij')
    mask = (i/half_width_cube_size)**2 + (j/half_width_cube_size)**2 + (k/half_height_cube_size)**2 <= 1
    
    tree_grid[
      i[mask]+tree_grid.shape[0]//2, 
      j[mask]+tree_grid.shape[1]//2, 
      k[mask] + int((stem_height-crown_offset) / self.cube_size + half_height_cube_size)
    ] = CellType.crown.value
    
  def add_columnar_tree(self, tree_grid: np.ndarray, tree_configuration: dict[str, float]):
    stem_height = tree_configuration["stem_height"]
    crown_diameter = tree_configuration["crown_width"]
    crown_height = tree_configuration["crown_height"]
    crown_offset = tree_configuration["crown_offset"]
    
    crown_radius = int(crown_diameter / 2 / self.cube_size)
    
    crown_height_range = np.arange(int(crown_height / self.cube_size))
    crown_range = np.arange(-crown_radius, crown_radius + 1)
    
    j, k = np.meshgrid(crown_range, crown_range, indexing='ij')
    mask = j**2 + k**2 <= crown_radius**2
    
    for i in crown_height_range:
      tree_grid[
        j[mask]+tree_grid.shape[0]//2, 
        k[mask]+tree_grid.shape[1]//2, 
        i + int((stem_height - crown_offset) / self.cube_size)
      ] = CellType.crown.value 
      
  def add_spreading_tree(self, tree_grid: np.ndarray, tree_configuration: dict[str, float]):
    """
    
    :param position: The (x, y, z) coordinates where the tree will be added.
    :type position: Tuple[int, int, int]
    :param stem_diameter: The diameter of the tree's stem.
    :type stem_diameter: float
    :param stem_height: The height of the tree's stem.
    :type stem_height: float
    :param crown_diameter: The diameter of the tree's crown.
    :type crown_diameter: float
    :return: None
    :rtype: None
    """
    
    stem_height = tree_configuration["stem_height"]
    crown_width = tree_configuration["crown_width"]
    crown_height = tree_configuration["crown_height"]
    crown_offset = tree_configuration["crown_offset"]
    
    half_width_cube_size = int(crown_width / 2 / self.cube_size)
    half_height_cube_size = int(crown_height / 2 / self.cube_size)
    
    crown_range_xy = np.arange(-half_width_cube_size, half_width_cube_size + 1)
    crown_range_z = np.arange(-half_height_cube_size, half_height_cube_size + 1)
    i, j, k = np.meshgrid(crown_range_xy, crown_range_xy, crown_range_z, indexing='ij')
    mask = (i/half_width_cube_size)**2 + (j/half_width_cube_size)**2 + (k/half_height_cube_size)**2 <= 1 & (k >= 0)
    
    tree_grid[
      i[mask]+tree_grid.shape[0]//2, 
      j[mask]+tree_grid.shape[1]//2, 
      k[mask] + int((stem_height-crown_offset) / self.cube_size)
    ] = CellType.crown.value
  
  def evaluate_forest(self, tree_configurations: List[Dict[str, Any]]):
    """
    Evaluates the forest by checking for potential collisions between trees and resolving them.
    This method sets the `evaluated_forest` attribute to True indicating that it does not have to be
    reevaluated, unless new trees are added to the scene.
    
    :return: None
    :rytpe: None
    """
    
    self.evaluated_forest = True
    
    tree_widths = [tree_configurations[tree[3]]["crown_width"] for tree in self.trees]
    
    max_range = np.max(tree_widths)
    
    tree_positions = KDTree([t[:3] for t in self.trees])
    
    pairs_to_evaluate: Set[Tuple[int, int]] = set()
    
    for i, tree in enumerate(self.trees):
      potential_collisions = tree_positions.query_ball_point(tree[:3], max_range + tree_widths[i])
      
      for collision_index in potential_collisions:
        if collision_index == i:
          continue
        pair = (min(i, collision_index), max(i, collision_index))
        pairs_to_evaluate.add(pair)
    
    pairs_to_evaluate_list = list(pairs_to_evaluate)
    random.shuffle(pairs_to_evaluate_list)
    
    for pair in pairs_to_evaluate_list:
      tree = self.trees[pair[0]]
      other_tree = self.trees[pair[1]]
      self.resolve_collision(tree, other_tree)
        
  def resolve_collision(self, tree1: Tuple[int, int, int, np.ndarray], tree2: Tuple[int, int, int, np.ndarray]):
    """
    Resolves the collision between two voxel grids representing trees.
    In this algorithm, some cells are marked as collision cells with specific values.
    After the algorithm is done, all collision cells will be either one or zero.  
    
    :param tree1: The first tree's position and voxel grid.
    :type tree1: Tuple[int, int, int, np.ndarray]
    :param tree2: The second tree's position and voxel grid.
    :type tree2: Tuple[int, int, int, np.ndarray]
    :return: None
    :rtype: None
    """
    
    x1, y1, z1, _, tree1_grid = tree1
    x2, y2, z2, _, tree2_grid = tree2
    
    translation = np.array([x1 - x2, y1 - y2, z1 - z2])
    
    tree1_filled_cells = np.argwhere(tree1_grid == CellType.crown.value)
    
    # translate to tree2 coordinate space
    tree1_filled_cells = tree1_filled_cells + translation
    
    tree2_collision_cells = self.get_colliding_cells(tree2_grid, tree1_filled_cells)
    tree1_collision_cells = tree2_collision_cells - translation
    
    if len(tree2_collision_cells) == 0:
      return
    
    tree1_collision_edge_cells = self.get_collision_edge_cells(tree1_grid, tree2_collision_cells - translation)
    tree2_collision_edge_cells = self.get_collision_edge_cells(tree2_grid, tree2_collision_cells)
    
    # set cells to 2 to distinguish them from other filled cells
    tree1_grid[tree1_collision_cells[:, 0], tree1_collision_cells[:, 1], tree1_collision_cells[:, 2]] = CellType.collision.value
    tree2_grid[tree2_collision_cells[:, 0], tree2_collision_cells[:, 1], tree2_collision_cells[:, 2]] = CellType.collision.value
    
    self.assign_collision_cells(tree1_grid, tree2_grid, tree1_collision_edge_cells, tree2_collision_edge_cells, translation)
  
  def get_colliding_cells(self, tree_grid: np.ndarray, filled_translated_cells: np.ndarray):
    contained_cells = self.trim_mask(tree_grid, filled_translated_cells)
    
    collision_indices = np.argwhere(tree_grid[contained_cells[:, 0], contained_cells[:, 1], contained_cells[:, 2]] == CellType.crown.value)
    collision_cells = contained_cells[collision_indices]
    
    return collision_cells.reshape(-1, 3)
  
  def trim_mask(self, tree_grid: np.ndarray, mask: np.ndarray):
    """
    Trims the given mask to only include indices that are within the bounds of the given tree grid.
    This is to prevent out of bound errors when accessing the tree grid.
    
    :param tree_grid: The voxel grid representing the tree.
    :type tree_grid: np.ndarray
    :param mask: The mask containing indices to be trimmed.
    :type mask: np.ndarray
    :return: The trimmed mask containing only indices within the tree grid.
    :rtype: np.ndarray
    """
    # get only the indices that are within the tree
    lower_limit = np.array([0, 0, 0])
    upper_limit = np.array([tree_grid.shape[0], tree_grid.shape[1], tree_grid.shape[2]])
    tree_contains_cell = np.all(mask >= lower_limit, axis=1) & np.all(mask < upper_limit, axis=1)
    
    return mask[tree_contains_cell]
    
  def get_collision_edge_cells(self, tree_grid: np.ndarray, collision_cells: set[np.ndarray]) -> np.ndarray:
    """
    Get the edge cells from the collision cells in the voxel grid.
    An edge cell is a cell that is adjacent to a collision cell.
    
    :param tree_grid: The voxel grid representing the tree.
    :type tree_grid: np.ndarray
    :param collision_cells: The set of collision cells in the voxel grid.
    :type collision_cells: set[np.ndarray]
    :return: The edge cells from the collision cells.
    :rtype: np.ndarray
    """
    
    # Define neighbor offsets (6-connectivity)
    neighbor_offsets = np.array([
        [1, 0, 0], [-1, 0, 0],
        [0, 1, 0], [0, -1, 0],
        [0, 0, 1], [0, 0, -1]
    ])
    
    # Get all neighbors
    neighbors = collision_cells[:, None, :] + neighbor_offsets[None, :, :]
    neighbors = neighbors.reshape(-1, 3)
    neighbors = self.trim_mask(tree_grid, neighbors)
    
    # Filter out neighbors that are not edge cells
    edge_mask = tree_grid[neighbors[:, 0], neighbors[:, 1], neighbors[:, 2]] == CellType.crown.value
    edge_cells = neighbors[edge_mask]
    
    return edge_cells
  
  def assign_collision_cells(self, 
                             tree1_grid: np.ndarray, 
                             tree2_grid: np.ndarray, 
                             tree1_collision_edge_cells: np.ndarray, 
                             tree2_collision_edge_cells: np.ndarray,
                             translation: np.ndarray):
    """
    Assign collision cells between two voxel grids representing trees.
    This function assigns collision cells between two voxel grids by iterating through a specified number of rounds.
    In each round, it selects a random collision edge cell from each tree and uses a sphere of a bounded random radius 
    to determine the cells to be assinged to a tree. The function ensures that the collision cells are updated in
    both grids accordingly.
    
    :param tree1_grid: The voxel grid representing the first tree.
    :type tree1_grid: np.ndarray
    :param tree2_grid: The voxel grid representing the second tree.
    :type tree2_grid: np.ndarray
    :param tree1_collision_edge_cells: The edge cells of the first tree.
    :type tree1_collision_edge_cells: np.ndarray
    :param tree2_collision_edge_cells: The edge cells of the second tree.
    :type tree2_collision_edge_cells: np.ndarray
    :param translation: The translation vector to align the grids.
    :type translation: np.ndarray
    :return: None
    :rtype: None
    """
    
    rounds = 5
    min_radius = 1
    max_radius = 3
    
    if len(tree1_collision_edge_cells) == 0 or len(tree2_collision_edge_cells) == 0:
      self.assign_rest_of_collision_cells(tree1_grid, tree2_grid, translation)
      return
  
    for round in range(rounds):
      sphere_radius = random.randint(min_radius, max_radius)
      sphere_cells = self.get_cells_for_sphere(sphere_radius)
      
      collision_edge_cell = random.choice(tree1_collision_edge_cells)
      mask = self.trim_mask(tree2_grid, sphere_cells + (collision_edge_cell + translation))
      mask = self.trim_mask(tree1_grid, mask - translation)
      conflicted_contained_cells = mask[tree1_grid[mask[:, 0], mask[:, 1], mask[:, 2]] == CellType.collision.value]
      tree1_grid[conflicted_contained_cells[:, 0], conflicted_contained_cells[:, 1], conflicted_contained_cells[:, 2]] = CellType.crown.value
      
      conflicted_contained_cells = conflicted_contained_cells + translation
      tree2_grid[conflicted_contained_cells[:, 0], conflicted_contained_cells[:, 1], conflicted_contained_cells[:, 2]] = CellType.no_tree.value

      collision_edge_cell = random.choice(tree2_collision_edge_cells)
      mask = self.trim_mask(tree1_grid, sphere_cells + (collision_edge_cell - translation))
      mask = self.trim_mask(tree2_grid, mask + translation)
      
      conflicted_contained_cells = mask[tree2_grid[mask[:, 0], mask[:, 1], mask[:, 2]] == CellType.collision.value]
      tree2_grid[conflicted_contained_cells[:, 0], conflicted_contained_cells[:, 1], conflicted_contained_cells[:, 2]] = CellType.crown.value
      
      conflicted_contained_cells = conflicted_contained_cells - translation
      tree1_grid[conflicted_contained_cells[:, 0], conflicted_contained_cells[:, 1], conflicted_contained_cells[:, 2]] = CellType.no_tree.value
      
    self.assign_rest_of_collision_cells(tree1_grid, tree2_grid, translation)
      
  def get_cells_for_sphere(self, radius: int):
    """
    Get the cells within a sphere of a given radius centered at the origin.
    
    :param radius: The radius of the sphere.
    :type radius: int
    :return: A numpy array of shape (N, 3) containing the points inside the sphere.
    :rtype: np.ndarray
    """
    
    x = np.arange(-radius, radius + 1)
    y = np.arange(-radius, radius + 1)
    z = np.arange(-radius, radius + 1)
    grid = np.array(np.meshgrid(x, y, z)).T.reshape(-1, 3)
    
    distances = np.sum(grid**2, axis=1)
    
    inside_sphere = grid[distances <= radius**2]
    
    return inside_sphere
    
  def assign_rest_of_collision_cells(self, 
                                     tree1_grid: np.ndarray, 
                                     tree2_grid: np.ndarray,
                                     translation: np.ndarray):
    """
    Assigns collision cells that were not assigned in the `assign_collision_cells` method.
    This function assigns collision cells between two voxel grids by comparing the distances of the cells from the 
    non-collision cells in each grid. It updates the collision cells in both grids based on which tree's cells are 
    closer to the non-collision cells.
    
    :param tree1_grid: The voxel grid representing the first tree.
    :type tree1_grid: np.ndarray
    :param tree2_grid: The voxel grid representing the second tree.
    :type tree2_grid: np.ndarray
    :param translation: The translation vector to align the grids.
    :type translation: np.ndarray
    :return: None
    :rtype: None
    """
    
    tree1_collision_cells = np.argwhere(tree1_grid == CellType.collision.value)

    mask = (tree1_grid != CellType.stem.value) | (tree1_grid != CellType.crown.value)
    tree1_distances = distance_transform_edt(mask)
    tree1_conflicted_distances = tree1_distances[tree1_collision_cells[:, 0], tree1_collision_cells[:, 1], tree1_collision_cells[:, 2]]
    
    tree2_collision_cells = tree1_collision_cells + translation
    mask = (tree2_grid != CellType.stem.value) | (tree2_grid != CellType.crown.value)
    tree2_distances = distance_transform_edt(mask)
    tree2_conflicted_distances = tree2_distances[tree2_collision_cells[:, 0], tree2_collision_cells[:, 1], tree2_collision_cells[:, 2]]
    
    tree1_cells_closer = tree1_collision_cells[tree1_conflicted_distances <= tree2_conflicted_distances]
    tree1_cells_farther = tree1_collision_cells[tree1_conflicted_distances > tree2_conflicted_distances]
    
    tree1_grid[tree1_cells_closer[:, 0], tree1_cells_closer[:, 1], tree1_cells_closer[:, 2]] = CellType.crown.value
    tree1_grid[tree1_cells_farther[:, 0], tree1_cells_farther[:, 1], tree1_cells_farther[:, 2]] = CellType.no_tree.value
    
    tree2_cells_closer = tree2_collision_cells[tree2_conflicted_distances < tree1_conflicted_distances]
    tree2_cells_farther = tree2_collision_cells[tree2_conflicted_distances >= tree1_conflicted_distances]
    
    tree2_grid[tree2_cells_closer[:, 0], tree2_cells_closer[:, 1], tree2_cells_closer[:, 2]] = CellType.crown.value
    tree2_grid[tree2_cells_farther[:, 0], tree2_cells_farther[:, 1], tree2_cells_farther[:, 2]] = CellType.no_tree.value
    
  def greedy_meshing(self, index: int):
    """
    Generate a mesh object using greedy meshing algorithm for a given index.
    This function generates a mesh object by capturing quads from the voxel grid and creating corresponding geometry 
    using Blender's bmesh module. The mesh is then positioned based on the tree's location.
    
    :param index: The index of the tree for which the mesh is to be generated.
    :type index: int
    :return: The generated mesh object.
    :rtype: bpy.types.Object
    """  
    
    quads = self.capture_quads(index)
    
    mesh = bpy.data.meshes.new(f"VoxelMesh")
    obj = bpy.data.objects.new(f"VoxelObject_{index}", mesh)

    # Prepare bmesh for geometry creation
    bm = bmesh.new()
    
    for quad in quads:
      x_start, y_start, z_start, x_end, y_end, z_end = quad
      
      x_start_position = x_start * self.cube_size - self.cube_size
      y_start_postion = y_start * self.cube_size - self.cube_size
      z_start_position = z_start * self.cube_size - self.cube_size
      x_end_position = x_end * self.cube_size 
      y_end_position = y_end * self.cube_size
      z_end_position = z_end * self.cube_size
      
      verts = [
        (x_start_position, y_start_postion, z_start_position),
        (x_end_position, y_start_postion, z_start_position),
        (x_end_position, y_end_position, z_start_position),
        (x_start_position, y_end_position, z_start_position),
        (x_start_position, y_start_postion, z_end_position),
        (x_end_position, y_start_postion, z_end_position),
        (x_end_position, y_end_position, z_end_position),
        (x_start_position, y_end_position, z_end_position)
      ]
      
      bm_verts = [bm.verts.new(v) for v in verts]

      # Create faces for the quad
      bm.faces.new([bm_verts[i] for i in [0, 1, 2, 3]])  # Bottom face
      bm.faces.new([bm_verts[i] for i in [4, 5, 6, 7]])  # Top face
      bm.faces.new([bm_verts[i] for i in [0, 1, 5, 4]])  # Front face
      bm.faces.new([bm_verts[i] for i in [2, 3, 7, 6]])  # Back face
      bm.faces.new([bm_verts[i] for i in [0, 3, 7, 4]])  # Left face
      bm.faces.new([bm_verts[i] for i in [1, 2, 6, 5]])  # Right face
    
    bm.to_mesh(mesh)
    bm.free()  
    
    obj.location = tuple(np.array(self.trees[index][:3]) * self.cube_size)
    return self.trees[index][3], obj
  
  def capture_quads(self, index: int):
    """
    Generates the quads used for the greedy meshing algorithm in the voxel grid for a given tree index.
    A quad is generated by merging the largest possible sections in all three dimensions with the order X, Y, Z.
    
    :param index: The index of the tree in the voxel grid.
    :type index: int
    :return: A list of captured quads, each represented as a tuple of six integers.
    :rtype: List[Tuple[int, int, int, int, int, int]]
    """
    
    instance_matrix = self.trees[index][-1]
    
    planes = self.capture_planes(instance_matrix)
    
    quads: List[Tuple[int, int, int, int, int, int]] = []
    while len(planes) > 0:
      z_position, plane_set = next(iter(planes.items()))
      x_start, y_start, x_end, y_end = next(iter(plane_set))
      quads.append(self.capture_quad(z_position, x_start, y_start, x_end, y_end, planes))
    
    return quads
  
  def capture_quad(self, z_position: int, x_start: int, y_start: int, x_end: int, y_end: int, planes: Dict[int, Set[Tuple[int, int, int, int]]]):
    """
    Captures a quad segment of a voxel grid by taking a X-Y plane and expanding it vertically until the segments no
    longer match.

    :param z_position: The initial z-position of the row segment.
    :type z_position: int
    :param x_start: The starting x-coordinate of the row segment.
    :type x_start: int
    :param y_start: The starting y-coordinate of the row segment.
    :type y_start: int
    :param x_end: The ending x-coordinate of the row segment.
    :type x_end: int
    :param y_end: The ending y-coordinate of the row segment.
    :type y_end: int
    :param planes: A dictionary mapping z-positions to sets of tuples representing row segments.
    :type planes: Dict[int, Set[Tuple[int, int, int, int]]]
    :return: The coordinates of the expanded quad segment.
    :rtype: Tuple[int, int, int, int, int, int]
    """
    offset_minus = 0
    while self.plane_matches_segment_length(z_position + offset_minus, x_start, x_end, y_start, y_end, planes): 
      offset_minus -= 1
    offset_minus += 1
    
    offset_plus = 1
    while self.plane_matches_segment_length(z_position + offset_plus, x_start, x_end, y_start, y_end, planes): 
      offset_plus += 1
    offset_plus -= 1
    
    return x_start, y_start, z_position + offset_minus, x_end, y_end, z_position + offset_plus
  
  def plane_matches_segment_length(self, z_position: int, x_start: int, x_end: int, y_start: int, y_end: int, planes: Dict[int, Set[Tuple[int, int, int, int]]]):
    """
    Checks if a plane segment matches the given x_start, y_start, and y_end within the specified z position.
    
    :param z_position: The z-coordinate of the plane segment.
    :type z_position: int
    :param x_start: The starting x-coordinate of the plane segment.
    :type x_start: int
    :param y_start: The starting y-coordinate of the plane segment.
    :type y_start: int
    :param y_end: The ending y-coordinate of the plane segment.
    :type y_end: int
    :param planes: A dictionary mapping z_position to sets of tuples representing plane segments.
    :type planes: Dict[int, Set[Tuple[int, int, int, int]]]
    :return: True if the plane segment matches and is removed, False otherwise.
    :rtype: bool
    """
    
    if z_position not in planes:
      return False 
    segments = planes[z_position]
    
    for (seg_x_start, seg_y_start, seg_x_end, seg_y_end) in segments:
      if seg_x_start == x_start and seg_x_end == x_end and seg_y_start == y_start and seg_y_end == y_end:
        planes[z_position].remove((seg_x_start, seg_y_start, seg_x_end, seg_y_end))
        if (len(planes[z_position]) == 0):
          del planes[z_position]
        return True
    
    return False
  
  def capture_planes(self, instance_matrix: np.array):
    """
    Captures planes from the given instance matrix by processing row segments on the x-axis.
    
    :param instance_matrix: The instance matrix to process.
    :type instance_matrix: np.array
    :return: A dictionary mapping z-coordinates to sets of tuples representing captured planes.
    :rtype: Dict[int, Set[Tuple[int, int, int, int]]]
    """
    
    rows = self.capture_rows(instance_matrix)
    
    planes: Dict[int, Set[Tuple[int, int, int, int]]] = {}
    while len(rows) > 0: 
      (y_position, z_position), row_set = next(iter(rows.items()))
      x_start, x_end = next(iter(row_set))
      plane = self.capture_plane(y_position, z_position, x_start, x_end, rows)
      if z_position in planes:
        planes[z_position].add(plane)
      else:
        planes[z_position] = {plane}
    
    return planes

  def capture_plane(self, y_position: int, z_position: int, x_start: int, x_end: int, rows: Dict[Tuple[int, int], Set[Tuple[int, int]]]):
    """
    Captures a plane by finding the continuous segment of rows that match the given x_start and x_end within the specified y and z positions.
    
    :param y_position: The y-coordinate of the row segment.
    :type y_position: int
    :param z_position: The z-coordinate of the row segment.
    :type z_position: int
    :param x_start: The starting x-coordinate of the row segment.
    :type x_start: int
    :param x_end: The ending x-coordinate of the row segment.
    :type x_end: int
    :param rows: A dictionary mapping (y_position, z_position) to sets of tuples representing row segments.
    :type rows: Dict[Tuple[int, int], Set[Tuple[int, int]]]
    :return: A tuple containing the starting x-coordinate, starting y-coordinate, the ending x-coordinate, and the ending y-coordinate.
    :rtype: Tuple[int, int, int, int]
    """
    
    
    # start with zero so the original row gets deleted as well.
    offset_minus = 0
    while self.row_matches_segment_length(y_position + offset_minus, z_position, x_start, x_end, rows): 
      offset_minus -= 1
    offset_minus += 1
    offset_plus = 1
    while self.row_matches_segment_length(y_position + offset_plus, z_position, x_start, x_end, rows): 
      offset_plus += 1
    offset_plus -= 1
    
    return x_start, y_position + offset_minus, x_end, y_position + offset_plus
  
  def row_matches_segment_length(self, y_position: int, z_position: int, x_start: int, x_end: int, rows: Dict[Tuple[int, int], Set[Tuple[int, int]]]):
    """
    Checks if a row segment matches the given x_start and x_end within the specified y and z posutuib.
    
    :param y_position: The y-coordinate of the row segment.
    :type y_position: int
    :param z_position: The z-coordinate of the row segment.
    :type z_position: int
    :param x_start: The starting x-coordinate of the row segment.
    :type x_start: int
    :param x_end: The ending x-coordinate of the row segment.
    :type x_end: int
    :param rows: A dictionary mapping (y_position, z_position) to sets of tuples representing row segments.
    :type rows: Dict[Tuple[int, int], Set[Tuple[int, int]]]
    :return: True if the row segment matches and is removed, False otherwise.
    :rtype: bool
    """
    
    if (y_position, z_position) not in rows:
      return False 
    segments = rows[(y_position, z_position)]
    
    for (seg_x_start, seg_x_end) in segments:
      if seg_x_start == x_start and seg_x_end == x_end:
        rows[(y_position, z_position)].remove((seg_x_start, seg_x_end))
        if (len(rows[(y_position, z_position)]) == 0):
          del rows[(y_position, z_position)]
        return True
    
    return False
   
  def capture_rows(self, instance_matrix: np.array):
    """
    Captures rows of a voxel grid by identifying the start and end positions of segments along the x-axis from the
    given instance matrix.
    
    :param instance_matrix: A 3D numpy array representing the voxel grid.
    :type instance_matrix: np.array
    :return: A dictionary mapping (y, z) coordinates to sets of tuples representing the start and end x-coordinates of row segments.
    :rtype: Dict[Tuple[int, int], Set[Tuple[int, int]]]
    """
    instance_matrix = (instance_matrix == CellType.crown.value) * 1
    
    diff_x = np.diff(instance_matrix, axis=0, append=0, prepend=0)
    
    start_x, start_y, start_z = np.where(diff_x > 0)
    end_x, end_y, end_z = np.where(diff_x < 0)
    
    border_positions = list(zip(start_x, start_y, start_z, np.zeros(len(start_x))))
    border_positions.extend(zip(end_x - 1, end_y, end_z, np.ones(len(end_x))))
    
    sorted_start_and_end = sorted(border_positions, key=lambda x: (x[0], x[3]))
    
    start_map: Dict[Tuple[int, int], int] = {}
    
    rows: Dict[Tuple[int, int], Set[Tuple[int, int]]] = {}
    for begin_or_end in sorted_start_and_end:
      if begin_or_end[3] == 0:
        start_map[(begin_or_end[1], begin_or_end[2])] = begin_or_end[0]
      else:
        start = start_map[(begin_or_end[1], begin_or_end[2])]
        if (begin_or_end[1], begin_or_end[2]) in rows:
          rows[(begin_or_end[1], begin_or_end[2])].add((start, begin_or_end[0]))
        else:
          rows[(begin_or_end[1], begin_or_end[2])] = {(start, begin_or_end[0])}
    
    return rows
    