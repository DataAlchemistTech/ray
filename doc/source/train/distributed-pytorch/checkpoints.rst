.. _train-checkpointing:

Saving and Loading Checkpoints
==============================

Ray Train provides a way to save :ref:`Checkpoints <checkpoint-api-ref>` during the training process. This is
useful for:

1. :ref:`Integration with Ray Tune <train-tune>` to use certain Ray Tune
   schedulers.
2. Running a long-running training job on a cluster of pre-emptible machines/pods.
3. Persisting trained model state to later use for serving/inference.
4. In general, storing any model artifacts.

By default, checkpoints will be persisted to local disk in the :ref:`log
directory <train-log-dir>` of each run.

.. _train-dl-saving-checkpoints:

Saving checkpoints
------------------

:ref:`Checkpoints <checkpoint-api-ref>` can be saved by calling ``train.report(metrics, checkpoint=Checkpoint(...))`` in the
training function. This will cause the checkpoint state from the distributed
workers to be saved on the ``Trainer`` (where your python script is executed).

The latest saved checkpoint can be accessed through the ``checkpoint`` attribute of
the :py:class:`~ray.air.result.Result`, and the best saved checkpoints can be accessed by the ``best_checkpoints``
attribute.

Concrete examples are provided to demonstrate how checkpoints (model weights but not models) are saved
appropriately in distributed training.


.. tab-set::

    .. tab-item:: Native PyTorch

        .. code-block:: python
            :emphasize-lines: 36, 37, 38, 39, 40, 41

            import ray.train.torch
            from ray import train
            from ray.air import Checkpoint, ScalingConfig
            from ray.train.torch import TorchTrainer

            import torch
            import torch.nn as nn
            from torch.optim import Adam
            import numpy as np

            def train_func(config):
                n = 100
                # create a toy dataset
                # data   : X - dim = (n, 4)
                # target : Y - dim = (n, 1)
                X = torch.Tensor(np.random.normal(0, 1, size=(n, 4)))
                Y = torch.Tensor(np.random.uniform(0, 1, size=(n, 1)))
                # toy neural network : 1-layer
                # wrap the model in DDP
                model = ray.train.torch.prepare_model(nn.Linear(4, 1))
                criterion = nn.MSELoss()

                optimizer = Adam(model.parameters(), lr=3e-4)
                for epoch in range(config["num_epochs"]):
                    y = model.forward(X)
                    # compute loss
                    loss = criterion(y, Y)
                    # back-propagate loss
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                    state_dict = model.state_dict()
                    checkpoint = Checkpoint.from_dict(
                        dict(epoch=epoch, model_weights=state_dict)
                    )
                    train.report({}, checkpoint=checkpoint)

            trainer = TorchTrainer(
                train_func,
                train_loop_config={"num_epochs": 5},
                scaling_config=ScalingConfig(num_workers=2),
            )
            result = trainer.fit()

            print(result.checkpoint.to_dict())
            # {'epoch': 4, 'model_weights': OrderedDict([('bias', tensor([-0.1215])), ('weight', tensor([[0.3253, 0.1979, 0.4525, 0.2850]]))]), '_timestamp': 1656107095, '_preprocessor': None, '_current_checkpoint_id': 4}

    .. tab-item:: PyTorch Lightning
        
        Ray Train leverages PyTorch Lightning's Callback interface to report metrics 
        and checkpoints. We provide a simple callback implementation that reports 
        ``on_train_epoch_end``.  

        Specifically, on each train epoch end, it 

        - collects all the logged metrics from ``trainer.callback_metrics`` 
        - saves a checkpoint via ``trainer.save_checkpoint`` 
        - reports to Ray Train via ``ray.train.report(metrics, checkpoint)`` 

        .. code-block:: python
            :emphasize-lines: 2,11,20,28,29,30,31,32
            
            import pytorch_lightning as pl
            from ray.train.lightning import RayTrainReportCallback
            from ray.train.torch import TorchTrainer
            from ray.train import CheckpointConfig, RunConfig

            class MyLightningModule(LightningModule):
                ...
                def on_validation_epoch_end(self):
                    ...
                    mean_acc = calculate_accuracy()
                    self.log("mean_accuracy", mean_acc, sync_dist=True)

            def train_func_per_worker():
                ...
                model = MyLightningModule(...)
                datamodule = MyLightningDataModule(...)

                trainer = pl.Trainer(
                    # ...
                    callbacks = [RayTrainReportCallback()]
                )
                trainer.fit(model, datamodule=datamodule)

            ray_trainer = TorchTrainer(
                train_func_per_worker,
                scaling_config=ScalingConfig(num_workers=2),
                run_config=RunConfig(
                    checkpoint_config=CheckpointConfig(
                        num_to_keep=2,
                        checkpoint_score_attribute="mean_accuracy",
                        checkpoint_score_order="max",
                    ),
                )
            )
            result = ray_trainer.fit()

        
        You can always get the saved checkpoint path from ``result.checkpoint`` and 
        ``result.best_checkpoints``.

        For more advanced usage (e.g. reporting at different frequency, reporting 
        customized checkpoint files), you can implement your own customized callback.
        Here is a simple example that reports a checkpoint every 3 epochs:

        .. code-block:: python
            
            import os
            import ray
            from ray.train import Checkpoint
            from tempfile import TemporaryDirectory
            from pytorch_lightning.callbacks import Callback

            class CustomRayTrainReportCallback(Callback):
                def on_train_epoch_end(self, trainer, pl_module):
                    if trainer.current_epoch % 3 != 0:
                        return 

                    with TemporaryDirectory() as tmpdir:
                        # Fetch metrics
                        metrics = trainer.callback_metrics
                        metrics = {k: v.item() for k, v in metrics.items()}

                        # Add customized metrics
                        metrics["epoch"] = trainer.current_epoch
                        metrics["custom_metric"] = 123
                    
                        # Save model checkpoint file to tmpdir
                        ckpt_path = os.path.join(tmpdir, "ckpt.pt")
                        trainer.save_checkpoint(ckpt_path, weights_only=False)

                        # Report to train session
                        checkpoint = Checkpoint.from_directory(tmpdir)
                        ray.train.report(metrics=metrics, checkpoint=checkpoint)
        
By default, checkpoints will be persisted to the :ref:`log directory <train-log-dir>` of each run.


.. _train-dl-configure-checkpoints:

Configuring checkpoints
-----------------------

For more configurability of checkpointing behavior (specifically saving
checkpoints to disk), a :py:class:`~ray.train.CheckpointConfig` can be passed into
``Trainer``.

.. literalinclude:: ../doc_code/key_concepts.py
    :language: python
    :start-after: __checkpoint_config_start__
    :end-before: __checkpoint_config_end__


.. seealso::

    See the :class:`~ray.train.CheckpointConfig` API reference.

.. note::

    If you want to save the top-k checkpoints with respect to a metric via
    :py:class:`~ray.train.CheckpointConfig`,
    please ensure that the metric is always reported together with the checkpoints.

**[Experimental] Distributed Checkpoints**: For model parallel workloads where the models do not fit in a single GPU worker,
it will be important to save and upload the model that is partitioned across different workers. You
can enable this by setting `_checkpoint_keep_all_ranks=True` to retain the model checkpoints across workers,
and `_checkpoint_upload_from_workers=True` to upload their checkpoints to cloud directly in :class:`~ray.train.CheckpointConfig`. This functionality works for any trainer that inherits from :class:`~ray.train.data_parallel_trainer.DataParallelTrainer`.



.. _train-dl-loading-checkpoints:

Loading checkpoints
-------------------

Checkpoints can be loaded into the training function in 2 steps:

1. From the training function, :func:`ray.train.get_checkpoint` can be used to access
   the most recently saved :py:class:`~ray.air.checkpoint.Checkpoint`. This is useful to continue training even
   if there's a worker failure.
2. The checkpoint to start training with can be bootstrapped by passing in a
   :py:class:`~ray.air.checkpoint.Checkpoint` to ``Trainer`` as the ``resume_from_checkpoint`` argument.


.. tab-set::

    .. tab-item:: Native PyTorch

        .. code-block:: python
            :emphasize-lines: 23, 25, 26, 29, 30, 31, 35

            import ray.train.torch
            from ray import train
            from ray.air import Checkpoint, ScalingConfig
            from ray.train.torch import TorchTrainer

            import torch
            import torch.nn as nn
            from torch.optim import Adam
            import numpy as np

            def train_func(config):
                n = 100
                # create a toy dataset
                # data   : X - dim = (n, 4)
                # target : Y - dim = (n, 1)
                X = torch.Tensor(np.random.normal(0, 1, size=(n, 4)))
                Y = torch.Tensor(np.random.uniform(0, 1, size=(n, 1)))

                # toy neural network : 1-layer
                model = nn.Linear(4, 1)
                criterion = nn.MSELoss()
                optimizer = Adam(model.parameters(), lr=3e-4)
                start_epoch = 0

                checkpoint = train.get_checkpoint()
                if checkpoint:
                    # assume that we have run the train.report() example
                    # and successfully save some model weights
                    checkpoint_dict = checkpoint.to_dict()
                    model.load_state_dict(checkpoint_dict.get("model_weights"))
                    start_epoch = checkpoint_dict.get("epoch", -1) + 1

                # wrap the model in DDP
                model = ray.train.torch.prepare_model(model)
                for epoch in range(start_epoch, config["num_epochs"]):
                    y = model.forward(X)
                    # compute loss
                    loss = criterion(y, Y)
                    # back-propagate loss
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                    state_dict = model.state_dict()
                    checkpoint = Checkpoint.from_dict(
                        dict(epoch=epoch, model_weights=state_dict)
                    )
                    train.report({}, checkpoint=checkpoint)

            trainer = TorchTrainer(
                train_func,
                train_loop_config={"num_epochs": 2},
                scaling_config=ScalingConfig(num_workers=2),
            )
            # save a checkpoint
            result = trainer.fit()

            # load checkpoint
            trainer = TorchTrainer(
                train_func,
                train_loop_config={"num_epochs": 4},
                scaling_config=ScalingConfig(num_workers=2),
                resume_from_checkpoint=result.checkpoint,
            )
            result = trainer.fit()

            print(result.checkpoint.to_dict())
            # {'epoch': 3, 'model_weights': OrderedDict([('bias', tensor([0.0902])), ('weight', tensor([[-0.1549, -0.0861,  0.4353, -0.4116]]))]), '_timestamp': 1656108265, '_preprocessor': None, '_current_checkpoint_id': 2}

    .. tab-item:: PyTorch Lightning

        .. code-block:: python
            :emphasize-lines: 11-17

            from ray import train
            from ray.train import Checkpoint, ScalingConfig
            from ray.train.torch import TorchTrainer
            from ray.train.lightning import RayTrainReportCallback
            from os.path import join

            def train_func_per_worker():
                model = MyLightningModule(...)
                datamodule = MyLightningDataModule(...)
                trainer = pl.Trainer(
                    ...
                    callbacks=[RayTrainReportCallback()]
                )

                checkpoint = train.get_checkpoint()
                if checkpoint:
                    with checkpoint.as_directory() as ckpt_dir:
                        ckpt_path = join(ckpt_dir, "checkpoint.ckpt")
                        trainer.fit(model, datamodule=datamodule, ckpt_path=ckpt_path)
                else:
                    trainer.fit(model, datamodule=datamodule)

            # Build a Ray Train Checkpoint
            # Suppose we have a Lightning checkpoint under ./ckpt_dir/checkpoint.ckpt
            checkpoint = Checkpoint.from_directory("./ckpt_dir/checkpoint.ckpt")

            # Resume training from checkpoint file
            ray_trainer = TorchTrainer(
                train_func_per_worker,
                scaling_config=ScalingConfig(num_workers=2),
                resume_from_checkpoint=checkpoint,
            )
            result = ray_trainer.fit()

