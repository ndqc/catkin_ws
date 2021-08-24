#!/usr/bin/env python
import rospy
from std_msgs.msg import String, Float32
from cv_bridge import CvBridge, CvBridgeError
import time
from sensor_msgs.msg import Image
import cv2.aruco as aruco
import cv2
import numpy as np
import parameter_control as params
from Calculate_angle import *
from aruco.msg import Angle
from scipy.ndimage import median_filter as mf
import matplotlib.pyplot as plt
import json
import joblib


def convert_to_parallel(distance_base, phi_data):
    pi = params.pi
    angle_aruco = phi_data.angle_aruco
    angle_base = phi_data.angle_base
    fix_rot = params.fix_rot

    if angle_aruco > 0:
        angle_rot_to_prll = 90 - angle_aruco
        angle_to_paralle = -angle_base + angle_rot_to_prll
        angle_to_paralle = angle_to_paralle * pi / 180
        distance_to_prll = distance_base * np.cos(angle_to_paralle)
        fix_phi = -fix_rot
    else:
        angle_rot_to_prll = 90 + angle_aruco
        angle_to_paralle = angle_base + angle_rot_to_prll
        angle_to_paralle = angle_to_paralle * pi / 180
        distance_to_prll = distance_base * np.cos(angle_to_paralle)
        fix_phi = fix_rot

    print('aruco {:.3f}\nangle to pll {:.3f}\nangle rot: {:.3f}\ndistance to pll: {:.3f}\ndistance base: {:.3f}'.format(
        angle_aruco,
        angle_to_paralle * 180 / pi,
        angle_rot_to_prll,
        distance_to_prll,
        distance_base))
    return distance_to_prll, angle_rot_to_prll, fix_phi


class kalman_filter:
    def __init__(self):
        self.A = 1.0
        self.B = 0
        self.Q = 1e-3  # given Q = cov(w)

        self.H = 1  # given H obser model
        self.R = 3e-1  # given R  = cov(v)
        self.P = 0
        self.U_hat = 0  # gia tri khoi tao uoc luong

    def update(self, U):
        # U la nhieu do duoc
        K = self.P * self.H / (self.H * self.P * self.H + self.R)
        self.U_hat = self.U_hat + K * (U - self.H * self.U_hat)
        self.P = (1 - K * self.H) * self.P + self.Q


class listener:
    def __init__(self):
        print("init")
        self.aruco_dict = aruco.Dictionary_get(aruco.DICT_4X4_1000)
        self.parameters = aruco.DetectorParameters_create()
        self._matrix_coefficients = params.mc
        self._distortion_coefficients = params.dc
        self.len_robot = params.len_robot
        self.pi = params.pi
        self.lastime = time.time()
        self.distance_pub = rospy.Publisher("/distance", Float32, queue_size=1)
        self.angleFiltered = rospy.Publisher("/angleFiltered", Float32, queue_size=1)
        self.Angle_pub = rospy.Publisher("/angle", Angle, queue_size=1)
        # self.detectImage = rospy.Publisher("/detectMarker", Image, queue_size=1)
        self.br = CvBridge()
        rospy.Subscriber("/camera_rside/color/image_raw", Image, self.callback1)
        rospy.Subscriber("/camera_rside/depth/image_rect_raw", Image, self.callback2)
        self.cv_depth = np.zeros((480, 848)) - 1
        self.kalman_filter = kalman_filter()
        self.save = []
        self.rf = joblib.load('/home/cros/catkin_ws/src/aruco/data_fiting/rf5.joblib')

    def callback1(self, data):
        cv_image = CvBridge().imgmsg_to_cv2(data, 'bgr8')
        img, ang, dis = self.detect_aruco(cv_image)
        # dis = self.rf.predict([[dis, ang.angle_base]])
        self.distance_pub.publish(dis)
        self.Angle_pub.publish(ang)
        if ang.angle_aruco != -1:
            self.kalman_filter.update(ang.angle_aruco)
        self.angleFiltered.publish(self.kalman_filter.U_hat)
        now = time.time()
        print('timing: {:.3f}\n'.format(now - self.lastime))
        self.lastime = now

    def callback2(self, data):
        cv_depth = CvBridge().imgmsg_to_cv2(data)
        # print(cv_depth.shape)
        h, w = cv_depth.shape[:2]
        cv_depth = cv_depth[71:391, 111:711]
        self.cv_depth = cv2.resize(cv_depth, (w, h))

    def detect_aruco(self, img):
        angle = Angle()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = aruco.detectMarkers(gray, self.aruco_dict, parameters=self.parameters)
        angle_base = {"102": -1, "50": -1, "888": -1}
        angle_cam = {"102": -1, "50": -1, "888": -1}
        distance_base = {"102": -1, "50": -1, "888": -1}
        pad_dr = 3

        for i in range(len(corners)):
            if ids[i] in [50, 102, 888]:
                rvec, tvec, markerPoints = aruco.estimatePoseSingleMarkers(corners[i], 0.02, self._matrix_coefficients,
                                                                           self._distortion_coefficients)

                # middle_image = (430, 244)
                middle_image = (424, 240)
                (rvec - tvec).any()
                center = (corners[i][0][0] + corners[i][0][1] + corners[i][0][2] + corners[i][0][3]) // 4
                c = (int(center[0]), int(center[1]))
                distance_roi = self.cv_depth[c[1] - pad_dr:c[1] + pad_dr, c[0] - pad_dr:c[0] + pad_dr]
                distance_roi = mf(distance_roi, (3, 3))
                distance = 0.001 * np.max(distance_roi)
                Ki = np.linalg.inv(self._matrix_coefficients)
                r2 = Ki.dot([center[0], middle_image[1], 1.0])
                r1 = Ki.dot([middle_image[0], middle_image[1], 1.0])
                cos_angle = r1.dot(r2) / (np.linalg.norm(r1) * np.linalg.norm(r2))
                angle_cam = np.arccos(cos_angle) * 180 / self.pi
                # print(ids[i], angle_cam)
                if center[0] > middle_image[0]:
                    angle_cam = -angle_cam
                # angle_base[str(ids[i][0])], distance_base[str(ids[i][0])] = convert_angle(angle_cam,
                #                                                                           leng_robot=self.len_robot,
                #                                                                           cam_offset=0.04,
                #                                                                           depth=distance)
                ac = convert_center_cam(angle_cam, 0.04, distance)
                angle_base[str(ids[i][0])], distance_base[str(ids[i][0])] = ac, distance

                # angle_aruco = 180 + rvec[0][0][1] * 180 / self.pi

                # print(tvec)
                # aruco.drawAxis(img, self._matrix_coefficients, self._distortion_coefficients, rvec, tvec, 0.01)
                # cv2.putText(img, str(ids[i]), c, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2, cv2.LINE_AA)

        pose, d2c = angle_pose_from_2_point(angle_base["102"], angle_base["50"], distance_base["102"],
                                            distance_base["50"])
        if pose != -1:
            pose = pose * params.regression_param[0] + params.regression_param[1]  # regression
        print('angle base: {} \ndistance: {} \naruco: {}'.format(angle_base, distance_base, pose))
        if distance_base['888'] != -1:
            a_888, d_888 = convert_center_cam_to_base(angle_base['888'], distance_base['888'])
            #d_888 = self.rf.predict([[d_888, a_888]])[0]
        angle.angle_cam = angle_cam
        angle.angle_base = a_888
        angle.angle_aruco = pose
        print('pose','angle base', pose, a_888)
        # convert_to_parallel(d_888, angle)
        # if d2c != -1:
        #   db = d2c
        # else:
        db = d_888
        return img, angle, db


def main():
    rospy.init_node('detect_markers', anonymous=True)
    listener()
    rospy.spin()


if __name__ == '__main__':
    print('chieu dai xe: ', params.len_robot)
    main()
