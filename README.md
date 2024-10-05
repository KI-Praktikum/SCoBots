# Successive Concepet Bottleneck Interface SCoBot
## Requirements
Scobots requires OCAtari and the local var ```'SCOBI_OBJ_EXTRACTOR'``` set as either ```OC_Atari``` or ```Noisy_OC_Atari``` as well as every dependency listed in the ```requirements.txt```.

Without agents SCoBots are not usable, so you can either download some pretrained agents from huggingface via the ```download_agents.sh``` script, or train one yourself, as explained in the usage guide.

## Usage
There are three python files which can be run directly. Each of them comes with a ```-h``` help flag.
1. train.py
2. eval.py
3. render_agent.py

### Training An Agent
Execute the train.py file to train an agent for a specific game, with a specified amount of cores and a specified seed.
The following example demonstrates the usage:
```bash
python train.py -g Pong -s 0 -env 8 -r env
```
The first three flags are required as input. With the help option the other flags can be displayed.
### Evaluating An Agent
The evaluate.py file evaluates an already trained agent, displaying the results afterwards and saving it in a dedicated file.

The following example demonstrates the usage of the previously trained agent:
```bash
python eval.py -g Pong -s 0 -t 10 -r env
```
The first three flags are required as input. With the help option the other flags can be displayed.

### Watching A Trained Agent
To visualize a trained agent playing a specified game the render_agent.py file can be executed.
Running the file will open and display the game played as a gif.

The following example demonstrates the usage of the previously trained + evaluated agent:
```bash
python render_agent.py -g Pong -s 0 -r env
```
The first three flags are required as input. With the help option the other flags can be displayed.

## Usage Of Checkpoints And Examples
Checkpoints are saved under ```resources/checkpoints```.
Each folder states in its name explicitly the specifications given for the training.
So e.g. the folder ```Pong_seed0_reward-human-version2``` denotes that the trained agent was trained with a ```seed``` of 0, its reward model is the ```human``` option, and that it is the second agent trained with these values.
So a usage with this agent would look like ```python eval.py -g Pong -s 0 -r human``` or ```python render_agent.py -g Pong -s 0 -r human```. This automatically picks the respectively latest trained agent named according to the values. For using a specific version the version flag has to be added.

With the checkpoint being stored accordingly named in the checkpoints folder, it will automaticlly be loaded and there is no need to provide an explicit storage path.

Furthermore during the training process every 10th epoch will be saved via pickle to ensure less frustrating training process in regards of potential crashes. These are saved separately in a sub-folder named ```training_checkpoints``` next to the ```best_model.zip``` and ```best_vecnormalize.pkl``` which are saved after a complete successful training process in. 
