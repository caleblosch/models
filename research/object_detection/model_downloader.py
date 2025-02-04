import wget
model_link = "http://download.tensorflow.org/models/object_detection/tf2/20210210/centernet_mobilenetv2fpn_512x512_coco17_od.tar.gz"
wget.download(model_link)
import tarfile
tar = tarfile.open('centernet_mobilenetv2fpn_512x512_coco17_od.tar.gz')
tar.extractall('.')
tar.close()
