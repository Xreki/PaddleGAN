import time
import paddle
import numbers
import numpy as np
from multiprocessing import Manager
from paddle.distributed import ParallelEnv

from paddle.io import DistributedBatchSampler
from ..utils.registry import Registry

DATASETS = Registry("DATASETS")


class DictDataset(paddle.io.Dataset):
    def __init__(self, dataset):
        self.dataset = dataset
        self.tensor_keys_set = set()
        self.non_tensor_keys_set = set()
        self.non_tensor_dict = Manager().dict()

        single_item = dataset[0]
        self.keys = single_item.keys()

        for k, v in single_item.items():
            if not isinstance(v, (numbers.Number, np.ndarray)):
                setattr(self, k, Manager().dict())
                self.non_tensor_keys_set.add(k)
            else:
                self.tensor_keys_set.add(k)

    def __getitem__(self, index):

        ori_map = self.dataset[index]

        tmp_list = []

        for k, v in ori_map.items():
            if isinstance(v, (numbers.Number, np.ndarray)):
                tmp_list.append(v)
            else:
                getattr(self, k).update({index: v})

        tmp_list.append(index)
        return tuple(tmp_list)

    def __len__(self):
        return len(self.dataset)

    def reset(self):
        for k in self.non_tensor_keys_set:
            setattr(self, k, Manager().dict())


class DictDataLoader():
    def __init__(self, dataset, batch_size, is_train, num_workers=4):

        self.dataset = DictDataset(dataset)

        place = paddle.fluid.CUDAPlace(ParallelEnv().dev_id) \
                    if ParallelEnv().nranks > 1 else paddle.fluid.CUDAPlace(0)

        sampler = DistributedBatchSampler(self.dataset,
                                          batch_size=batch_size,
                                          shuffle=True if is_train else False,
                                          drop_last=True if is_train else False)

        self.dataloader = paddle.io.DataLoader(self.dataset,
                                               batch_sampler=sampler,
                                               places=place,
                                               num_workers=num_workers)

        self.batch_size = batch_size

    def __iter__(self):

        self.dataset.reset()

        for i, data in enumerate(self.dataloader):
            return_dict = {}
            j = 0
            for k in self.dataset.keys:
                if k in self.dataset.tensor_keys_set:
                    return_dict[k] = data[j] if isinstance(data,
                                                           (list,
                                                            tuple)) else data
                    j += 1
                else:
                    return_dict[k] = self.get_items_by_indexs(k, data[-1])
            yield return_dict

    def __len__(self):
        return len(self.dataloader)

    def get_items_by_indexs(self, key, indexs):
        if isinstance(indexs, paddle.Variable):
            indexs = indexs.numpy()
        current_items = []
        items = getattr(self.dataset, key)

        for index in indexs:
            current_items.append(items[index])

        return current_items


def build_dataloader(cfg, is_train=True):
    dataset = DATASETS.get(cfg.name)(cfg)

    batch_size = cfg.get('batch_size', 1)
    num_workers = cfg.get('num_workers', 0)

    dataloader = DictDataLoader(dataset, batch_size, is_train, num_workers)

    return dataloader
