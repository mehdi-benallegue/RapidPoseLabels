import argparse
import os
import cv2
import numpy as np
import transforms3d.quaternions as tfq
import transforms3d.affines as tfa

class DatasetWriter:
    def __init__(self, output_dir):
        """
        Constructor for Writer class.
        Input arguments:
        output_dir - path to output directory
        """
        self.output_dir = output_dir
        #create sub-directories if they dont exist
        for dir_name in ["bboxes", "center", "scale", "label", "frames"]:
            if not os.path.isdir(os.path.join(self.output_dir, dir_name)):
                os.makedirs(os.path.join(self.output_dir, dir_name));

    def writeToDisk(self, sample, index):
        """
        Function to write the generated sample (keypoint, center, scale and the RGB image)
        in a format as expected by the ObjectKeypointTrainer training module
        (https://github.com/rohanpsingh/ObjectKeypointTrainer#preparing-the-dataset).
        Bounding-boxes are saved in the format as expected by darknet-yolov3
        (https://github.com/AlexeyAB/darknet#how-to-train-to-detect-your-custom-objects).
        Input arguments:
        sample - labeled sample (RGB image, (keypoint, center pos, scale))
        index  - counter for naming images
        """
        rgb_image = sample[0]
        kpt_label = sample[1][0]
        cen_label = sample[1][1]
        sca_label = sample[1][2]
        width = rgb_image.shape[1]
        height = rgb_image.shape[0]
        #write bounding box for yolo
        bboxfile = open(os.path.join(self.output_dir, 'bboxes', 'frame_' + repr(index).zfill(5) + '.txt'), 'w')
        bboxfile.write('0\t' + repr(cen_label[0]/width) + '\t' + repr(cen_label[1]/height) + '\t' +
                       repr(sca_label*200/width) + '\t' + repr(sca_label*200/height) + '\n')
        bboxfile.close()
        #write center to center/center_0####.txt
        centerfile = os.path.join(self.output_dir, 'center', 'center_' + repr(index).zfill(5) + '.txt')
        np.savetxt(centerfile, cen_label)
        #write scale to scale/scales_0####.txt
        scalesfile = os.path.join(self.output_dir, 'scale', 'scales_' + repr(index).zfill(5) + '.txt')
        np.savetxt(scalesfile, np.asarray([sca_label]))
        #write keypoints to label/label_0####.txt
        labelfile = os.path.join(self.output_dir, 'label', 'label_' + repr(index).zfill(5) + '.txt')
        np.savetxt(labelfile, kpt_label)
        #write RGB image to frames/frame_0####.txt
        cv2.imwrite(os.path.join(self.output_dir, 'frames', 'frame_' + repr(index).zfill(5) + '.jpg'), rgb_image)
        return


class Annotations:
    def __init__(self, dataset_path, input_arr_path, visualize=False):
        """
        Constructor for Annotations class.
        Input arguments:
        dataset_path   - path to root dataset directory
        input_arr_path - path to input npz zipped archive
        visualize      - set 'True' to visualize
        """
        self.dataset_path = dataset_path
        self.input_array  = np.load(input_arr_path)
        self.visualize    = visualize

        #read camera intrinsics matrix from camera.txt in root directory
        self.cam_mat = np.eye(3)
        with open(os.path.join(dataset_path, 'camera.txt'), 'r') as file:
            camera_intrinsics = file.readlines()[0].split()
            camera_intrinsics = list(map(float, camera_intrinsics))
        self.cam_mat[0,0] = camera_intrinsics[0]
        self.cam_mat[1,1] = camera_intrinsics[1]
        self.cam_mat[0,2] = camera_intrinsics[2]
        self.cam_mat[1,2] = camera_intrinsics[3]

        #get number of scenes and number of keypoints
        self.num_scenes = self.input_array['ref'].shape[0]
        self.num_keypts = self.input_array['ref'].shape[2]

        #paths to each of the scene dirs inside root dir
        self.list_of_scene_dirs = [d for d in os.listdir(dataset_path) if os.path.isdir(os.path.join(dataset_path, d))]
        self.list_of_scene_dirs.sort()
        self.list_of_scene_dirs = self.list_of_scene_dirs[:self.num_scenes]
        print("List of scenes: ", self.list_of_scene_dirs)
        print("Number of scenes: ", self.num_scenes)
        print("Number of keypoints: ", self.num_keypts)

        #excect images to be 640x480
        self.width = 640
        self.height = 480

        #bounding-box needs to scaled up to avoid excessive cropping
        self.bbox_scale = 1.5
        #define a ratio of labeled samples to produce
        self.ratio = 10

    def visualize_sample(self, sample):
        """
        Visualize using opencv draw functions if self.visualize is set True.
        Input arguments:
        sample - labeled sample (RGB image, (keypoint, center pos, scale))
        """
        input_img = sample[0]
        keypts = sample[1][0]
        bbox_cn = sample[1][1]
        bbox_sd = sample[1][2]*200
        #draw keypoints
        for p in keypts:
            cv2.circle(input_img, tuple(map(int, p)), 5, (255, 0, 0), -1)
        #draw bounding-box
        cv2.rectangle(input_img,
                      (int(bbox_cn[0]-(bbox_sd/2)), int(bbox_cn[1]-(bbox_sd/2))),
                      (int(bbox_cn[0]+(bbox_sd/2)), int(bbox_cn[1]+(bbox_sd/2))),
                      (0,255,0), 2)
        cv2.imshow('window', input_img)
        cv2.waitKey(50)
        return

    def project3Dto2D(self, input_points, input_pose):
        """
        Function to project the sparse object model onto the RGB image
        according to the provided pose of the object model in camera frame.
        Input arguments:
        input_points - object model 3D points
        input_pose   - pose of object model in camera frame
        Returns:
        (u, v) pos of all object keypoints, bounding box center and scaled side.
        """
        #project 3D points to 2D image plane
        rvec,_ = cv2.Rodrigues(input_pose[:3, :3])
        tvec = input_pose[:3,3]
        imgpts,_ = cv2.projectPoints(input_points, rvec, tvec, self.cam_mat, None)
        keypts = np.transpose(np.asarray(imgpts), (1,0,2))[0]

        #estimate a square box using mean and min-max in x- and y-
        bbox_cn = keypts.mean(0)
        xmin, ymin = keypts.min(0)
        xmax, ymax = keypts.max(0)
        if xmin<0: xmin=0
        if ymin<0: ymin=0
        if xmax>=(self.width-1):  xmax=(self.width-1)
        if ymax>=(self.height-1): ymax=(self.height-1)
        bbox_cn = ((xmax+xmin)/2, (ymax+ymin)/2)
        bbox_sd = max((xmax-xmin), (ymax-ymin))*self.bbox_scale
        return keypts, bbox_cn, bbox_sd/200.0
        
    def process_input(self, debug=False):
        """
        Function to extract data from the input array.
        Input array is the output of the optimization step
        which holds the generated sparse model of the object
        and the relative scene transformations.
        Set debug=True to print metadata.
        """
        #get scene transforamtions from input array
        out_ts = self.input_array['res'][ :(self.num_scenes-1)*3].reshape((self.num_scenes-1, 3))
        out_qs = self.input_array['res'][(self.num_scenes-1)*3 : (self.num_scenes-1)*7].reshape((self.num_scenes-1, 4))
        out_Ts = np.asarray([tfa.compose(t, tfq.quat2mat(q), np.ones(3)) for t,q in zip(out_ts, out_qs)])
        #get object model from input_array
        out_Ps = self.input_array['res'][(self.num_scenes-1)*7 : ].reshape((self.num_keypts, 3))

        #this is the object model
        self.object_model = out_Ps
        #these are the relative scene transformations
        self.scene_tfs    = np.concatenate((np.eye(4)[np.newaxis,:], out_Ts))
        if debug:
            np.set_printoptions(precision=5, suppress=True)
            print("--------\n--------\n--------")
            print("Output translations:\n", out_ts)
            print("Output quaternions:\n", out_qs)
            print("Output points:\n", out_Ps, out_Ps.shape)
            print("--------\n--------\n--------")
        return

    def generate_labels(self):
        """
        Main function to generate labels for RGB images according to provided input array.
        Returns a list of samples where each sample is tuple of the RGB image and the 
        associated label, where each label is a tuple of the keypoints, center and scale.
        """
        samples = []
        #iterate through a zip of list of scene dirs and the relative scene tfs
        for data_dir_idx, (cur_scene_dir, sce_T) in enumerate(zip(self.list_of_scene_dirs, self.scene_tfs)):

            #read the names of image frames in this scene
            with open(os.path.join(self.dataset_path, cur_scene_dir, 'associations.txt'), 'r') as file:
                img_name_list = file.readlines()

            #read the camera pose corresponding to each frame
            with open(os.path.join(self.dataset_path, cur_scene_dir, 'camera.poses'), 'r') as file:
                cam_pose_list = [list(map(float, line.split()[1:])) for line in file.readlines()]

            #generate labels only for a fraction of total images in scene
            zipped_list = list(zip(img_name_list, cam_pose_list))[::self.ratio]
            for idx, (img_name, cam_pose) in enumerate(zipped_list):
                #read the RGB images using opencv
                img_name = img_name.split()
                rgb_im_path = os.path.join(self.dataset_path, cur_scene_dir, img_name[3])
                input_rgb_image = cv2.resize(cv2.imread(rgb_im_path), (self.width, self.height))
                #compose 4x4 camera pose matrix
                cam_T = tfa.compose(np.asarray(cam_pose[:3]), tfq.quat2mat(np.asarray([cam_pose[-1]] + cam_pose[3:-1])), np.ones(3))
                #get 2D positions of keypoints, centers and scale of bounding box
                label = self.project3Dto2D(self.object_model, np.dot(np.linalg.inv(cam_T), sce_T))
                samples.append((input_rgb_image, label))

                #visualize if required
                if self.visualize:
                    self.visualize_sample((input_rgb_image, label))

            print("Created {} labeled samples from dataset {} (with {} raw samples).".format(len(zipped_list), data_dir_idx, len(img_name_list)))
        return samples

if __name__ == '__main__':

    # get command line arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help='path to root dir of raw dataset')
    ap.add_argument("--input", required=True, help='path to input .npz zipped archive')
    ap.add_argument("--output", required=True, help='path to output directory')
    ap.add_argument("--visualize", action='store_true', help='to visualize each label')
    opt = ap.parse_args()

    #set up Annotations
    lab = Annotations(opt.dataset, opt.input, opt.visualize)
    #extract useful information from input array
    lab.process_input(False)
    #generate labels and writes to output directory
    samples = lab.generate_labels()

    #write each sample to disk
    writer = DatasetWriter(opt.output)
    for counter, sample in enumerate(samples):
        writer.writeToDisk(sample, counter)
        print("Saved sample: {}".format(repr(counter).zfill(5)), end="\r", flush=True)
    print("Total number of samples generated: {}".format(len(samples)))
