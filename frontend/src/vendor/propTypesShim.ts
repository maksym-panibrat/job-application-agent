type Validator = {
  (...args: unknown[]): null
  isRequired: (...args: unknown[]) => null
}

function validator(): Validator {
  const fn = (() => null) as Validator
  fn.isRequired = () => null
  return fn
}

const PropTypes = {
  bool: validator(),
  func: validator(),
  node: validator(),
  number: validator(),
  object: validator(),
  string: validator(),
  oneOf: () => validator(),
}

export default PropTypes
