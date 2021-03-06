from pathlib import Path
import json
import pickle
def check_dir(dir_name):
    if not Path(dir_name).is_dir():
        raise FileNotFoundError

def check_file(fn):
    if not Path(fn).is_file():
        raise FileNotFoundError

def file_exsit(fn):
    return Path(fn).is_file()

def dir_exsit(fn):
    return Path(fn).is_dir()



def load_pkl(filename):
    if Path(filename).is_file():
        data=None
        with open(filename,'rb') as file:
            data=pickle.load(file)
            file.close()
            return data
    raise FileNotFoundError

def save_pkl(filename,obj,overwrite=True):
    def write():
        with open(filename,'wb') as file:
            pickle.dump(obj,file)
            file.flush()
            file.close()

    if Path(filename).is_file() and overwrite:
        write()
        return

    write()

def load_json(filename):
    if Path(filename).is_file():
        with open(filename) as f:
            return json.load(f)
    raise FileNotFoundError

def save_json(filename,obj,overwrite=True):
    def write():
        with open(filename,'w',encoding="utf8") as file:
            json.dump(obj,file,indent=4)

    if Path(filename).is_file and overwrite:
        write()
        return
    write()
