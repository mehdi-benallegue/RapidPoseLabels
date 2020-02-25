import numpy as np
import transforms3d as tf
import cv2
import os
import sys
import open3d as o3d
import scipy.optimize
import optimize


class Pose:
    def __init__(self, dataset_path, scale):
        self.scene_imgs = []
        self.scene_cams = []
        self.scene_plys = []
        self.scene_kpts = []
        self.pts_in_3d  = []
        self.scale = scale

        self.camera_intrinsics = []
        with open(os.path.join(dataset_path, 'camera.txt'), 'r') as file:
            self.camera_intrinsics = file.readlines()[0].split()
            self.camera_intrinsics = list(map(float, self.camera_intrinsics))

    def convert_2Dto3D(self):
        self.select_vec = []
        pts_3d = []
        for rgb, dep, pts in self.scene_imgs:
            w = []
            for pt in pts:
                pt3d_z = (dep[pt[1], pt[0]])*(1.0/self.scale)
                if pt!=[-1, -1] and pt3d_z!=0:
                    pt3d_x = (pt[0] - self.camera_intrinsics[2])*(pt3d_z/self.camera_intrinsics[0])
                    pt3d_y = (pt[1] - self.camera_intrinsics[3])*(pt3d_z/self.camera_intrinsics[1])
                    pt3d = [pt3d_x, pt3d_y, pt3d_z]
                    self.select_vec.append(1)
                else:
                    pt3d = [0, 0, 0]
                    self.select_vec.append(0)
                w.append(pt3d)
            pts_3d.append(w)
        pts_3d = np.asarray(pts_3d).transpose(0,2,1)
        self.pts_in_3d = pts_3d

    def transform_points(self):
        scene_tf = []
        for scene_pts, pose in zip(self.pts_in_3d, self.scene_cams):
            pose_t = np.asarray(pose[:3])[:, np.newaxis]
            pose_q = np.asarray([pose[-1]] + pose[3:-1])
            scene_tf.append(tf.quaternions.quat2mat(pose_q).dot(scene_pts) + pose_t.repeat(scene_pts.shape[1], axis=1))
        self.scene_kpts = np.asarray(scene_tf)

    def visualize_object(self, scene_ply_path, scene_obj_kpts):
        vis_mesh_list = []
        scene_cloud = o3d.io.read_point_cloud(scene_ply_path)
        vis_mesh_list.append(scene_cloud)
        for keypt in scene_obj_kpts.transpose():
            keypt_mesh = o3d.geometry.TriangleMesh.create_sphere(radius=0.01)
            keypt_mesh.translate(keypt[0])
            keypt_mesh.paint_uniform_color([0.1, 0.1, 0.7])
            vis_mesh_list.append(keypt_mesh)
        o3d.visualization.draw_geometries(vis_mesh_list)

    def compute(self):
        total_kpt_count  = len(self.select_vec)
        found_kpt_count  = len(np.nonzero(self.select_vec)[0])
        selection_matrix = np.zeros((found_kpt_count*3, total_kpt_count*3))

        print("Total number of keypts: ", total_kpt_count)
        print("Keypts localized in 3D: ", found_kpt_count)
        print("Size of selection matrix: ", selection_matrix.shape)

        #generate selection matrix from select_vec
        row = 0
        for idx, flag in enumerate(self.select_vec):
            if flag:
                selection_matrix[row:row+3, (idx*3):(idx*3)+3] = np.eye(3)
                row+=3

        np.savez("saved_gui_output", kpts=self.scene_kpts, sm=selection_matrix)
        if False:
            f = np.load("saved_gui_output.npz")
            self.scene_kpts = f['kpts']
            selection_matrix = f['sm']

        #initialize quaternions and translations for each scene
        scene_t_ini = np.array([[0, 0, 0]]).repeat(self.scene_kpts.shape[0], axis=0)
        scene_q_ini = np.array([[1, 0, 0, 0]]).repeat(self.scene_kpts.shape[0], axis=0)
        scene_P_ini = np.array([[[0, 0, 0]]]).repeat(self.scene_kpts.shape[2], axis=0)

        res = optimize.predict(self.scene_kpts, scene_t_ini, scene_q_ini, scene_P_ini, selection_matrix)

        #output from optimization
        len_ts = scene_t_ini[1:].size
        len_qs = scene_q_ini[1:].size
        len_Ps = scene_P_ini.size
        output_vec = res.x
        if res.success:
            print("--------\n--------\n--------")
            out_ts = output_vec[:len_ts].reshape(scene_t_ini[1:, :].shape)
            out_qs = output_vec[len_ts:len_ts+len_qs].reshape(scene_q_ini[1:, :].shape)
            out_Ps = output_vec[len_ts+len_qs:].reshape(scene_P_ini.shape)
            object_model = out_Ps.transpose()
            print("SUCCESS")
            np.set_printoptions(precision=5, suppress=True)
            print("Output translations:\n", out_ts)
            print("Output quaternions:\n", out_qs)
            print("Object Model:\n", object_model, object_model.shape)
            print("--------\n--------\n--------")

        #visualize the generated object model in first scene
        self.visualize_object(self.scene_plys[0], object_model)
        return res.success