import numpy as np
import os
import random
import random
import time
import torch
import torch.optim as optim
import sys
from os import path
sys.path.append(path.dirname(path.dirname(path.dirname(path.abspath(__file__)))))
from torch.utils.tensorboard import SummaryWriter
from torch.distributions import Categorical
from tqdm import tqdm
from rtpt import RTPT
from scobi import Environment
from . import networks

#from agent import Agent

EPS = np.finfo(np.float32).eps.item()
PATH_TO_OUTPUTS = os.getcwd() + "/checkpoints/"
if not os.path.exists(PATH_TO_OUTPUTS):
    os.makedirs(PATH_TO_OUTPUTS)

model_name = lambda training_name : PATH_TO_OUTPUTS + training_name + "_model.pth"

dev = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class ExperienceBuffer():
    def __init__(self, size, gamma):
        self.observations = []
        self.rewards = []
        self.values = []
        self.logprobs = []

        self.returns = []
        self.advantages = []
        self.ptr, self.max_size = 0, size
        self.gamma = gamma


    def add(self, observation, reward, value, logprob):
        if self.ptr > self.max_size:
            print("buffer error")
            exit()
        self.observations.append(observation)
        self.rewards.append(reward)
        self.values.append(value)
        self.logprobs.append(logprob)
        self.ptr += 1


    def finalize(self):
        self.rewards = np.array(self.rewards)
        vals = torch.cat(self.values).detach().cpu().numpy()
        R = 0
        for r in self.rewards[::-1]:
            R = r + self.gamma * R 
            self.returns.insert(0, R)
        self.returns = np.array(self.returns)
        self.advantages = self.returns - vals


    def get(self):
        mean = self.advantages.mean()
        std = self.advantages.std()
        self.advantages = (self.advantages - mean) / std

        data = { "obs"  : torch.as_tensor(self.observations, device=dev),
                 "rets" : torch.as_tensor(self.returns, device=dev),
                 "advs" : torch.as_tensor(self.advantages, device=dev), 
                 "logps": torch.cat(self.logprobs),
                 "vals" : torch.cat(self.values)
                                        }
        return data
    

    def reset(self):
        self.ptr = 0
        self.observations = []
        self.rewards = []
        self.values = []
        self.logprobs = []
        self.returns = []
        self.advantages = []


def select_action(features, policy, random_tr = -1, n_actions=3):
    input = torch.tensor(features, device=dev).unsqueeze(0)     
    probs = policy(input)
    sampler = Categorical(probs)
    action = sampler.sample()
    log_prob = sampler.log_prob(action)
    # select action when no random action should be selected
    if random.random() <= random_tr:
       action = random.randint(0, n_actions - 1)
    else:
        action = action.item()
    # return action and log prob
    return action, log_prob, probs.detach().cpu().numpy()




def train(cfg):
    cfg.exp_name = cfg.exp_name + "-seed" + str(cfg.seed)
    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    writer = SummaryWriter(os.getcwd() + cfg.logdir + cfg.exp_name)

    # init env to get params for policy net
    env = Environment(cfg.env_name, interactive=cfg.scobi_interactive, focus_dir=cfg.scobi_focus_dir, focus_file=cfg.scobi_focus_file)
    n_actions = env.action_space.n
    env.reset()
    obs, _, _, _, info, _ = env.step(1)
    print("EXPERIMENT")
    print(">> Selected algorithm: REINFORCE")
    print('>> Experiment name:', cfg.exp_name)
    print('>> Seed:', torch.initial_seed())
    print(">> Random Action probability:", cfg.train.random_action_p)
    print('>> Gamma:', cfg.train.gamma)
    print('>> Learning rate:', cfg.train.learning_rate)
    print("ENVIRONMENT")
    print('>> Action space: ' + str(env.action_space_description))
    print(">> Observation Vector Length:", len(obs))

    # init fresh policy and optimizer
    policy_net = networks.PolicyNet(len(obs), cfg.train.policy_h_size, n_actions).to(dev)
    value_net = networks.ValueNet(len(obs), cfg.train.value_h_size, 1).to(dev)
    policy_optimizer = optim.Adam(policy_net.parameters(), lr=cfg.train.learning_rate)
    value_optimizer = optim.Adam(value_net.parameters(), lr=cfg.train.learning_rate)
    i_epoch = 1
    # overwrite if checkpoint exists
    model_path = model_name("val_" + cfg.exp_name)
    if os.path.isfile(model_path):
        print("{} does exist, loading ... ".format(model_path))
        checkpoint = torch.load(model_path)
        value_net.load_state_dict(checkpoint['value'])
        value_optimizer.load_state_dict(checkpoint['optimizer'])
        i_epoch = checkpoint['episode']
    model_path = model_name("pol_" + cfg.exp_name)
    if os.path.isfile(model_path):
        print("{} does exist, loading ... ".format(model_path))
        checkpoint = torch.load(model_path)
        policy_net.load_state_dict(checkpoint['policy'])
        policy_optimizer.load_state_dict(checkpoint['optimizer'])
        i_epoch = checkpoint['episode']
        i_epoch += 1

    print("TRAINING")
    print('>> Epochs:', cfg.train.num_episodes)
    print('>> Steps per Epoch:', cfg.train.steps_per_episode)
    print('>> Logging Interval (Steps):', cfg.train.log_steps)
    print('>> Checkpoint Interval (Epochs):', cfg.train.save_every)
    print('>> Current Epoch:', i_epoch)
    print("Training started...")
    # reinit agent with loaded policy model
    running_return = None
    # tfboard logging buffer
    tfb_nr_buffer = 0
    tfb_pnl_buffer = 0
    tfb_vnl_buffer = 0
    tfb_pne_buffer = 0
    tfb_step_buffer = 0
    tfb_policy_updates_counter = 0
    buffer = ExperienceBuffer(cfg.train.max_steps_per_trajectory, cfg.train.gamma)

    # save model helper function
    def save_models(training_name, episode):
        if not os.path.exists(PATH_TO_OUTPUTS):
            os.makedirs(PATH_TO_OUTPUTS)
        pol_model_path = model_name("pol_" + training_name)
        val_model_path = model_name("val_" + training_name)
        #print("Saving {}".format(model_path))
        torch.save({
                'policy': policy_net.state_dict(),
                'episode': episode,
                'optimizer': policy_optimizer.state_dict()
                }, pol_model_path)
        torch.save({
                'value': value_net.state_dict(),
                'episode': episode,
                'optimizer': value_optimizer.state_dict()
                }, val_model_path)

    def update_models(data):
        torch.autograd.set_detect_anomaly(True)
        obss, rets, advs, logps, vals =  data["obs"], data["rets"], data["advs"], data["logps"], data["vals"]

        policy_optimizer.zero_grad()
        policy_loss = (-logps * advs).mean()
        policy_loss.backward()
        torch.nn.utils.clip_grad_norm_(policy_net.parameters(), cfg.train.clip_norm)
        policy_optimizer.step()

        ep_len = len(rets)
        val_iters = max(1, int(ep_len / 50))
        for i in range(val_iters):
            value_optimizer.zero_grad()
            value_loss = ((rets - vals)**2).mean()
            value_loss.backward()
            torch.nn.utils.clip_grad_norm_(policy_net.parameters(), cfg.train.clip_norm)
            value_optimizer.step()
            vals = torch.squeeze(value_net(obss.unsqueeze(0)), -1)
        return policy_loss, value_loss

    # training loop
    rtpt = RTPT(name_initials='SeSz', experiment_name=cfg.exp_name, max_iterations=cfg.train.num_episodes)
    rtpt.start()
    while i_epoch <= cfg.train.num_episodes:
        stdout_nr_buffer = 0
        stdout_pnl_buffer = 0
        stdout_vnl_buffer = 0
        stdout_pne_buffer = 0
        stdout_step_buffer = 0
        stdout_policy_updates_counter = 0
        sum_ep_duration = 0
        sum_int_duration = 0
        sum_pol_duration = 0
        i_episode_step = 0
        while i_episode_step < cfg.train.steps_per_episode:
            entropies = []
            ep_return = 0
            int_duration = 0
            i_trajectory_step = 0
            incomplete_traj = False
            int_s_time = time.perf_counter()
            while i_trajectory_step < cfg.train.max_steps_per_trajectory:
                
                # interaction
                action, log_prob, probs = select_action(obs, policy_net, cfg.train.random_action_p, n_actions)
                value_net_input = torch.tensor(obs, device=dev).unsqueeze(0)     
                value_estimation = torch.squeeze(value_net(value_net_input), -1)
                new_obs, natural_reward, terminated, truncated, info, _ = env.step(action)

                # collection
                entropy = -np.sum(list(map(lambda p : p * (np.log(p) / np.log(n_actions)) if p[0] != 0 else 0, probs)))
                buffer.add(obs, natural_reward, value_estimation, log_prob)
                entropies.append(entropy)
                ep_return += natural_reward
                i_trajectory_step += 1
                i_episode_step += 1
                obs = new_obs

                # tfboard logging
                if i_episode_step % cfg.train.log_steps == 0 and tfb_policy_updates_counter > 0:
                    global_step = (i_epoch - 1) * cfg.train.steps_per_episode + i_episode_step
                    avg_nr = tfb_nr_buffer / tfb_policy_updates_counter
                    avg_pnl = tfb_pnl_buffer / tfb_policy_updates_counter
                    avg_vnl = tfb_vnl_buffer / tfb_policy_updates_counter
                    avg_pne = tfb_pne_buffer / tfb_policy_updates_counter
                    avg_step = tfb_step_buffer / tfb_policy_updates_counter
                    writer.add_scalar('rewards/avg_return', avg_nr, global_step)
                    writer.add_scalar('loss/avg_policy_net', avg_pnl, global_step)
                    writer.add_scalar('loss/avg_value_net', avg_vnl, global_step)
                    writer.add_scalar('loss/avg_policy_net_entropy', avg_pne, global_step)
                    writer.add_scalar('various/avg_steps', avg_step, global_step)
                    tfb_nr_buffer = 0
                    tfb_pnl_buffer = 0
                    tfb_vnl_buffer = 0
                    tfb_pne_buffer = 0
                    tfb_step_buffer = 0
                    tfb_policy_updates_counter = 0

                # break conditions
                if terminated or truncated:
                    break
                if i_episode_step == cfg.train.steps_per_episode:
                    incomplete_traj = True
                    break
            
            buffer.finalize()
            # policy update
            int_duration += time.perf_counter() - int_s_time
            pol_s_time = time.perf_counter()
            data = buffer.get()
            policy_loss, value_loss = update_models(data)
            buffer.reset()
            env.reset()
            pol_duration = time.perf_counter() - pol_s_time
            policy_loss = policy_loss.detach()
            value_loss = value_loss.detach()

            ep_entropy = np.mean(entropies)
            ep_duration = int_duration + pol_duration

            if not incomplete_traj:
                tfb_policy_updates_counter += 1
                tfb_nr_buffer += ep_return
                tfb_pnl_buffer += policy_loss
                tfb_vnl_buffer += value_loss
                tfb_pne_buffer += ep_entropy
                tfb_step_buffer += i_trajectory_step

                stdout_policy_updates_counter += 1
                stdout_nr_buffer += ep_return
                stdout_pnl_buffer += policy_loss
                stdout_vnl_buffer += value_loss
                stdout_pne_buffer += ep_entropy
                stdout_step_buffer += i_trajectory_step

                sum_ep_duration += ep_duration
                sum_int_duration += int_duration
                sum_pol_duration += pol_duration
                # update logging data
                if running_return is None:
                    running_return = ep_return
                else:
                    running_return = 0.05 * ep_return + (1 - 0.05) * running_return



        # checkpointing
        checkpoint_str = ""
        if i_epoch % cfg.train.save_every == 0:
            save_models(cfg.exp_name, i_epoch)
            checkpoint_str = "checkpoint"

        # episode stats
        c = stdout_policy_updates_counter
        print('Epoch {}:\tRunning Return: {:.2f}\tavgReturn: {:.2f}\tavgEntropy: {:.2f}\tavgValueNetLoss: {:.2f}\tavgSteps: {:.2f}\tDuration: {:.2f} [ENV: {:.2f} | P_UPDATE: {:.2f}]\t{}'.format(
            i_epoch, running_return, stdout_nr_buffer / c, stdout_pne_buffer / c, stdout_vnl_buffer / c, stdout_step_buffer / c, sum_ep_duration, sum_int_duration, sum_pol_duration, checkpoint_str))
        

        i_epoch += 1
        rtpt.step()


# eval function, returns trained model
def eval_load(cfg):
    print('Experiment name:', cfg.exp_name)
    print('Evaluating Mode')
    print('Seed:', cfg.seed)
    print("Random Action probability:", cfg.train.random_action_p)
    # disable gradients as we will not use them
    torch.set_grad_enabled(False)
    # init env
    env = Environment(cfg.env_name, focus_dir="focusfiles")
    n_actions = env.action_space.n
    env.reset()
    obs, _, _, _, info, _ = env.step(1)
    print("Make hidden layer in nn:", cfg.train.make_hidden)
    policy_net = networks.PolicyNet(len(obs), cfg.train.policy_h_size, n_actions).to(dev)
    # load if exists
    model_path = model_name("pol_" + cfg.exp_name + "-seed" + str(cfg.seed))
    if os.path.isfile(model_path):
        print("{} does exist, loading ... ".format(model_path))
        checkpoint = torch.load(model_path)
        policy_net.load_state_dict(checkpoint['policy'])
        i_epoch = checkpoint['episode']
        print('Epochs trained:', i_epoch)
    policy_net.eval()
    return policy_net
