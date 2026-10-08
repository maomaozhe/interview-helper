"""Finite JSON choice grammar. Contains no model or filesystem side effects."""
import json


def decode_finite_answer(text, questions):
    """Validate native JSON generation without repairing or guessing values."""
    answers = json.loads(text)
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("INVALID_ANSWER_FIELDS")
    if any(not isinstance(value, str) or value not in questions[key]["criteria"]
           for key, value in answers.items()):
        raise ValueError("INVALID_ANSWER_CHOICE")
    return answers


class FiniteChoiceSequence:
    def __init__(self,tokenizer,questions,max_tokens=192):
        self.prefix=tokenizer.encode("[",add_special_tokens=False)
        self.eos=tokenizer.eos_token_id
        self.fields=[]
        for index,(key,question) in enumerate(questions.items()):
            suffix="]" if index==len(questions)-1 else ","
            paths={choice:tokenizer.encode(json.dumps(choice,ensure_ascii=False)+suffix,add_special_tokens=False)
                   for choice in question["criteria"]}
            if not paths or any(not path for path in paths.values()): raise ValueError("INVALID_CHOICE_GRAMMAR")
            self.fields.append((key,paths))
        if len(self.prefix)+sum(max(map(len,paths.values())) for _,paths in self.fields)+1>max_tokens:
            raise ValueError("OPTION_SEQUENCE_TOO_LONG")

    def allowed(self,generated):
        if generated[:min(len(generated),len(self.prefix))]!=self.prefix[:len(generated)]:
            raise ValueError("INVALID_CHOICE_PREFIX")
        if len(generated)<len(self.prefix): return [self.prefix[len(generated)]]
        position=len(self.prefix)
        for _,paths in self.fields:
            tail=generated[position:]
            complete=next((path for path in paths.values() if tail[:len(path)]==path),None)
            if complete is not None:
                position+=len(complete);continue
            matching=[path for path in paths.values() if path[:len(tail)]==tail and len(path)>len(tail)]
            if not matching: raise ValueError("INVALID_CHOICE_PREFIX")
            return sorted({path[len(tail)] for path in matching})
        if len(generated)!=position: raise ValueError("INVALID_CHOICE_SUFFIX")
        return [self.eos]

    def parse(self,generated):
        if generated and generated[-1]==self.eos: generated=generated[:-1]
        position=len(self.prefix);choices={};ranges={}
        if generated[:position]!=self.prefix: raise ValueError("INVALID_CHOICE_PREFIX")
        for key,paths in self.fields:
            selected=next((choice for choice,path in paths.items() if generated[position:position+len(path)]==path),None)
            if selected is None: raise ValueError("MODEL_OUTPUT_INCOMPLETE")
            start=position;position+=len(paths[selected]);choices[key]=selected;ranges[key]=(start,position)
        if position!=len(generated): raise ValueError("INVALID_CHOICE_SUFFIX")
        return choices,ranges
